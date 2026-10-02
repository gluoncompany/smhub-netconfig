#!/usr/bin/env python3
# Network config app for SMHUB - IPv4 per interface, Wi-Fi radio/connect, smhub.json export.
# Python stdlib only. Uses NetworkManager through nmcli. Changes are auto-reverted unless confirmed.
import ipaddress, json, os, re, socket, subprocess, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("NC_DATA", "/opt/netconfig/data")
PORT = int(os.environ.get("NC_PORT", "8097"))
NMCLI = os.environ.get("NC_NMCLI", "nmcli")
CONFIRM_SECONDS = int(os.environ.get("NC_CONFIRM_SECONDS", "90"))
HOSTNAME_FILE = os.environ.get("NC_HOSTNAME_FILE", "/etc/hostname")
PENDING_FILE = os.path.join(DATA_DIR, "pending.json")

lock = threading.Lock()
pending = None          # {"id", "deadline", "summary", "rollback": [[args...], ...], "new_ips": [...]}
last_event = None       # {"key", "args"} shown in the UI (e.g. reverted)


class NmError(Exception):
    def __init__(self, key, *args):
        super().__init__(key)
        self.key = key
        self.args_ = [str(a) for a in args]


def nm(*args, timeout=30, check=True):
    try:
        r = subprocess.run([NMCLI, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NmError("err_nmcli", e)
    if check and r.returncode != 0:
        raise NmError("err_nmcli", (r.stderr or r.stdout).strip())
    return r.stdout


def split_terse(line):
    """Split a terse nmcli line on unescaped ':' and unescape."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def fields(text):
    """Parse 'FIELD:value' lines (nmcli -t -f ... show)."""
    d = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.replace("\\:", ":").replace("\\\\", "\\")
        k = re.sub(r"\[\d+\]$", "", k)
        if k in d:
            d[k] = d[k] + [v] if isinstance(d[k], list) else [d[k], v]
        else:
            d[k] = v
    return d


def as_list(v):
    if v in (None, ""):
        return []
    return v if isinstance(v, list) else [v]


def nm_list(v):
    """nmcli comma-separated list values."""
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


# ---------------------------------------------------------------- state

def get_state(client_ip=None):
    devices = {}
    for line in nm("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device").splitlines():
        p = split_terse(line)
        if len(p) >= 4 and p[1] in ("ethernet", "wifi"):
            devices[p[0]] = {"device": p[0], "type": p[1], "state": p[2], "active": p[3]}
    for dev, d in devices.items():
        info = fields(nm("-t", "-f", "GENERAL.HWADDR,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", dev, check=False))
        d["mac"] = info.get("GENERAL.HWADDR", "")
        d["ip4"] = as_list(info.get("IP4.ADDRESS"))
        d["gateway"] = info.get("IP4.GATEWAY", "") if not isinstance(info.get("IP4.GATEWAY"), list) else info["IP4.GATEWAY"][0]
        d["dns"] = as_list(info.get("IP4.DNS"))
        d["client"] = bool(client_ip) and any(a.split("/")[0] == client_ip for a in d["ip4"])

    conns = []
    for line in nm("-t", "-f", "NAME,UUID,TYPE,DEVICE,AUTOCONNECT", "connection", "show").splitlines():
        p = split_terse(line)
        if len(p) < 5 or p[2] not in ("802-3-ethernet", "802-11-wireless"):
            continue
        c = {"name": p[0], "uuid": p[1], "type": "wifi" if p[2] == "802-11-wireless" else "ethernet",
             "device": p[3], "autoconnect": p[4] == "yes"}
        keys = "connection.interface-name,ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns"
        if c["type"] == "wifi":
            keys += ",802-11-wireless.ssid,802-11-wireless-security.key-mgmt"
        s = fields(nm("-t", "-f", keys, "connection", "show", c["uuid"], check=False))
        c["ifname"] = s.get("connection.interface-name", "")
        c["method"] = s.get("ipv4.method", "auto")
        c["addresses"] = nm_list(s.get("ipv4.addresses", ""))
        c["gateway"] = s.get("ipv4.gateway", "")
        c["dns"] = nm_list(s.get("ipv4.dns", ""))
        if c["type"] == "wifi":
            c["ssid"] = s.get("802-11-wireless.ssid", "")
            c["security"] = s.get("802-11-wireless-security.key-mgmt", "")
        conns.append(c)

    radio = nm("-t", "-f", "WIFI", "radio", check=False).strip()
    wifi_on = radio == "enabled" and any(c["autoconnect"] for c in conns if c["type"] == "wifi")
    with lock:
        pend = None
        if pending:
            pend = {"id": pending["id"], "remaining": max(0, int(pending["deadline"] - time.time())),
                    "summary": pending["summary"], "new_ips": pending["new_ips"],
                    "old_ips": pending.get("old_ips", [])}
    return {"hostname": read_hostname(), "devices": list(devices.values()), "connections": conns,
            "wifi_radio": radio == "enabled", "wifi_enabled": wifi_on, "client_ip": client_ip, "pending": pend,
            "event": last_event, "confirm_seconds": CONFIRM_SECONDS}


def read_hostname():
    try:
        return open(HOSTNAME_FILE).read().strip()
    except OSError:
        return socket.gethostname()


def conn_settings(uuid):
    keys = "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns,connection.autoconnect"
    s = fields(nm("-t", "-f", keys, "connection", "show", uuid))
    return {k: s.get(k, "") for k in keys.split(",")}


def find_conn(uuid):
    for line in nm("-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show").splitlines():
        p = split_terse(line)
        if len(p) >= 4 and p[1] == uuid:
            return {"name": p[0], "uuid": p[1], "type": p[2], "device": p[3]}
    raise NmError("err_no_conn", uuid)


# ---------------------------------------------------------------- pending changes / rollback

def save_pending():
    os.makedirs(DATA_DIR, exist_ok=True)
    if pending:
        with open(PENDING_FILE, "w") as f:
            json.dump(pending, f)
    elif os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)


def run_steps(steps):
    for s in steps:
        nm(*s, timeout=60, check=False)


def start_change(summary, apply_steps, rollback_steps, new_ips, old_ips=()):
    """Apply in background (so the HTTP reply gets out first) and arm the rollback timer."""
    global pending, last_event
    with lock:
        if pending:
            raise NmError("err_pending")
        pending = {"id": str(int(time.time() * 1000)), "deadline": time.time() + CONFIRM_SECONDS + 3,
                   "summary": summary, "rollback": rollback_steps, "new_ips": new_ips, "old_ips": list(old_ips)}
        last_event = None
        save_pending()
        pid = pending["id"]

    def worker():
        time.sleep(3)
        run_steps(apply_steps)
        while True:
            time.sleep(1)
            with lock:
                if not pending or pending["id"] != pid:
                    return
                if time.time() >= pending["deadline"]:
                    break
        revert(pid, "ev_reverted_timeout")

    threading.Thread(target=worker, daemon=True).start()
    return {"id": pid, "remaining": CONFIRM_SECONDS, "summary": summary, "new_ips": new_ips, "old_ips": list(old_ips)}


def revert(pid=None, reason="ev_reverted"):
    global pending, last_event
    with lock:
        if not pending or (pid and pending["id"] != pid):
            return False
        steps = pending["rollback"]
        summary = pending["summary"]
        pending = None
        save_pending()
        last_event = {"key": reason, "args": [summary.get("name", "")]}
    run_steps(steps)
    return True


def confirm(pid):
    global pending, last_event
    with lock:
        if not pending or pending["id"] != pid:
            return False
        last_event = {"key": "ev_confirmed", "args": [pending["summary"].get("name", "")]}
        pending = None
        save_pending()
    return True


def recover_on_start():
    """If the service restarted (or the hub rebooted) during a pending change, roll it back."""
    global pending
    try:
        with open(PENDING_FILE) as f:
            p = json.load(f)
    except (OSError, ValueError):
        return
    pending = p
    revert(p["id"], "ev_reverted_restart")


# ---------------------------------------------------------------- actions

def restore_steps(uuid, old):
    steps = [["connection", "modify", uuid,
              "ipv4.method", old["ipv4.method"] or "auto",
              "ipv4.addresses", old["ipv4.addresses"],
              "ipv4.gateway", old["ipv4.gateway"],
              "ipv4.dns", old["ipv4.dns"].replace(",", " ")]]
    return steps


def set_ipv4(body):
    uuid = str(body.get("uuid", ""))
    c = find_conn(uuid)
    method = body.get("method")
    if method not in ("auto", "manual"):
        raise NmError("err_method")
    old = conn_settings(uuid)
    if old["ipv4.method"] not in ("auto", "manual", ""):
        raise NmError("err_locked", old["ipv4.method"])
    new_ips = []
    if method == "manual":
        try:
            iface = ipaddress.ip_interface(str(body.get("address", "")).strip())
        except ValueError:
            raise NmError("err_address", body.get("address", ""))
        if iface.version != 4 or iface.network.prefixlen in (0, 32):
            raise NmError("err_address", body.get("address", ""))
        if iface.ip in (iface.network.network_address, iface.network.broadcast_address):
            raise NmError("err_address", str(iface))
        gw = str(body.get("gateway", "")).strip()
        if gw:
            try:
                g = ipaddress.ip_address(gw)
            except ValueError:
                raise NmError("err_gateway", gw)
            if g not in iface.network:
                raise NmError("err_gateway_net", gw, str(iface.network))
        dns = [x for x in re.split(r"[\s,;]+", str(body.get("dns", "")).strip()) if x]
        for d in dns:
            try:
                ipaddress.ip_address(d)
            except ValueError:
                raise NmError("err_dns", d)
        apply_mod = ["connection", "modify", uuid, "ipv4.method", "manual",
                     "ipv4.addresses", str(iface), "ipv4.gateway", gw, "ipv4.dns", " ".join(dns)]
        new_ips = [str(iface.ip)]
    else:
        apply_mod = ["connection", "modify", uuid, "ipv4.method", "auto",
                     "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""]
    active = bool(c["device"])
    apply = [apply_mod] + ([["connection", "up", uuid]] if active else [])
    rollback = restore_steps(uuid, old) + ([["connection", "up", uuid]] if active else [])
    summary = {"action": "ipv4", "name": c["name"], "device": c["device"], "method": method,
               "address": new_ips[0] if new_ips else ""}
    old_ips = []
    if active:
        info = fields(nm("-t", "-f", "IP4.ADDRESS", "device", "show", c["device"], check=False))
        old_ips = [a.split("/")[0] for a in as_list(info.get("IP4.ADDRESS"))]
    return start_change(summary, apply, rollback, new_ips, old_ips)


def wifi_info():
    """Wi-Fi device, active profile and all Wi-Fi profiles with their autoconnect flag."""
    wdev, active = None, None
    for line in nm("-t", "-f", "DEVICE,TYPE,CONNECTION", "device").splitlines():
        p = split_terse(line)
        if len(p) >= 3 and p[1] == "wifi" and not wdev:
            wdev, active = p[0], (p[2] or None)
    profiles = []
    for line in nm("-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT", "connection", "show").splitlines():
        p = split_terse(line)
        if len(p) >= 4 and p[2] == "802-11-wireless":
            profiles.append({"name": p[0], "uuid": p[1], "autoconnect": p[3] == "yes"})
    radio = nm("-t", "-f", "WIFI", "radio", check=False).strip() == "enabled"
    return wdev, active, profiles, radio


def wifi_enabled(profiles, radio):
    return radio and any(p["autoconnect"] for p in profiles)


def set_radio(body):
    """Enable/disable Wi-Fi WITHOUT rfkill (the aic8800 driver does not recover from 'nmcli radio wifi off'):
    disabling = autoconnect off on every Wi-Fi profile + disconnect the device; enabling = the reverse."""
    on = bool(body.get("enabled"))
    wdev, active, profiles, radio = wifi_info()
    if not wdev:
        raise NmError("err_no_wifi")
    current = wifi_enabled(profiles, radio)
    best = active or next((p["uuid"] for p in profiles if p["autoconnect"]), None) or \
        (profiles[0]["uuid"] if profiles else None)
    restore_ac = [["connection", "modify", p["uuid"], "connection.autoconnect", "yes" if p["autoconnect"] else "no"]
                  for p in profiles]
    if on == current:
        if on and not active and best:
            threading.Thread(target=run_steps, args=([["connection", "up", best]],), daemon=True).start()
        return None
    summary = {"action": "radio", "name": "Wi-Fi", "enabled": on}
    if not on:
        apply = [["connection", "modify", p["uuid"], "connection.autoconnect", "no"] for p in profiles] + \
                [["device", "disconnect", wdev]]
        rollback = restore_ac + ([["radio", "wifi", "on"]] if not radio else []) + \
                   ([["connection", "up", best]] if best else [])
    else:
        target = [p for p in profiles if p["uuid"] == best] or profiles
        apply = ([["radio", "wifi", "on"]] if not radio else []) + \
                [["connection", "modify", p["uuid"], "connection.autoconnect", "yes"] for p in target] + \
                ([["connection", "up", best]] if best else [])
        rollback = restore_ac + [["device", "disconnect", wdev]]
    return start_change(summary, apply, rollback, [])


def wifi_scan():
    nm("device", "wifi", "rescan", check=False, timeout=20)
    time.sleep(2)
    out, seen = [], {}
    for line in nm("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN", "device", "wifi", "list", check=False).splitlines():
        p = split_terse(line)
        if len(p) < 5 or not p[1]:
            continue
        item = {"in_use": p[0] == "*", "ssid": p[1], "signal": int(p[2] or 0), "security": p[3], "channel": p[4]}
        if p[1] not in seen or item["signal"] > seen[p[1]]["signal"]:
            seen[p[1]] = item
    out = sorted(seen.values(), key=lambda x: -x["signal"])
    return out


def wifi_connect(body):
    ssid = str(body.get("ssid", "")).strip()
    if not ssid or len(ssid) > 32:
        raise NmError("err_ssid")
    password = str(body.get("password", ""))
    if password and not (8 <= len(password) <= 63):
        raise NmError("err_psk")
    wdev = next((d["device"] for d in get_state()["devices"] if d["type"] == "wifi"), None)
    if not wdev:
        raise NmError("err_no_wifi")
    prev = None
    for line in nm("-t", "-f", "DEVICE,CONNECTION", "device").splitlines():
        p = split_terse(line)
        if len(p) >= 2 and p[0] == wdev and p[1]:
            prev = p[1]
    name = "netconfig-" + re.sub(r"[^A-Za-z0-9_.-]", "_", ssid)[:40]
    apply = [["connection", "delete", name],
             ["connection", "add", "type", "wifi", "ifname", wdev, "con-name", name, "ssid", ssid,
              "802-11-wireless.hidden", "yes" if body.get("hidden") else "no"]
             + (["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password] if password else []),
             ["connection", "up", name]]
    rollback = [["connection", "down", name], ["connection", "delete", name]]
    if prev:
        rollback.append(["connection", "up", prev])
    summary = {"action": "wifi", "name": ssid, "device": wdev}
    return start_change(summary, apply, rollback, [])


def smhub_json(include_password=False):
    st = get_state()
    out = {"hostname": st["hostname"], "network": {}}
    eth = next((c for c in st["connections"] if c["type"] == "ethernet" and
                (c["ifname"] == "eth0" or c["device"] == "eth0")), None)
    if eth:
        e = {"method": "manual" if eth["method"] == "manual" else "auto"}
        if e["method"] == "manual" and eth["addresses"]:
            e["address"] = eth["addresses"][0]
            if eth["gateway"]:
                e["gateway"] = eth["gateway"]
            if eth["dns"]:
                e["dns"] = ";".join(eth["dns"])
        out["network"]["eth0"] = e
    wifi = next((c for c in st["connections"] if c["type"] == "wifi" and c["device"]), None) or \
        next((c for c in st["connections"] if c["type"] == "wifi"), None)
    if wifi and wifi.get("ssid"):
        w = {"ssid": wifi["ssid"], "hidden": False}
        if include_password and wifi.get("security"):
            psk = nm("-s", "-g", "802-11-wireless-security.psk", "connection", "show", wifi["uuid"], check=False).strip()
            if psk:
                w["password"] = psk
        out["network"]["wlan0"] = w
    return out


# ---------------------------------------------------------------- HTTP

def err(e):
    return {"key": e.key, "args": e.args_} if isinstance(e, NmError) else {"key": "err_generic", "args": [str(e)]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def client_ip(self):
        try:
            return self.connection.getsockname()[0]
        except OSError:
            return None

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        path, _, query = self.path.partition("?")
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(APP_DIR, "index.html"), "rb") as f:
                    self.send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self.send(200, get_state(self.client_ip()))
            elif path == "/api/wifi/scan":
                self.send(200, {"networks": wifi_scan()})
            elif path == "/api/smhub.json":
                data = json.dumps(smhub_json("password=1" in query), indent=2).encode()
                self.send(200, data, "application/json",
                          {"Content-Disposition": 'attachment; filename="smhub.json"'})
            else:
                self.send(404, {"error": {"key": "err_not_found", "args": []}})
        except Exception as e:
            self.send(500, {"error": err(e)})

    def do_POST(self):
        path = self.path.split("?")[0]
        b = self.body()
        try:
            if path == "/api/ipv4":
                self.send(202, {"pending": set_ipv4(b)})
            elif path == "/api/wifi/radio":
                self.send(202, {"pending": set_radio(b)})
            elif path == "/api/wifi/connect":
                self.send(202, {"pending": wifi_connect(b)})
            elif path == "/api/confirm":
                self.send(200 if confirm(str(b.get("id", ""))) else 409, {})
            elif path == "/api/revert":
                self.send(200 if revert(str(b.get("id", "")) or None, "ev_reverted") else 409, {})
            else:
                self.send(404, {"error": {"key": "err_not_found", "args": []}})
        except NmError as e:
            self.send(400, {"error": err(e)})
        except Exception as e:
            self.send(500, {"error": err(e)})


if __name__ == "__main__":
    recover_on_start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
