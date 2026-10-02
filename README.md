# netconfig – Network configuration for SMHUB

A small app for **SMLIGHT SMHUB OS** to manage the hub network from the panel, without SSH or an SD card:

- **Static IP or DHCP per interface** (Ethernet, Wi-Fi): address/prefix, gateway and DNS, with validation.
- **Automatic rollback**: every change must be confirmed within 90 s, otherwise the previous configuration is restored. Before the connection drops, the app shows a link to the new address; after a rollback, a link back to the previous one. Pending changes are also rolled back if the service restarts or the hub reboots.
- **Wi-Fi**: enable/disable, scan and connect to a network (WPA2-PSK, open or hidden).
- **Export `smhub.json`** in the SD-card configuration format supported by SMHUB OS (hostname, eth0, Wi-Fi; the Wi-Fi password is only included on request).
- Profiles managed by the system in other modes (e.g. `usb0` in `shared` mode for USB gadget networking) are shown read-only.
- Shows up in the hub panel like any other app (sidebar + iframe), with a **Spanish / English** UI and light/dark theme.

## Requirements

- SMHUB with **SMHUB OS 1.0.x** (tested on a **SMHUB Nano MG24**, SMHUB OS 1.0.2).
- No extra dependencies: NetworkManager (`nmcli`, included in SMHUB OS) and the Python 3 standard library.

## How it works

| File | Purpose |
|---|---|
| `app/server.py` | HTTP server and API on port 8097. Reads and changes the configuration with `nmcli`, runs the confirm/rollback timer and keeps pending changes in `/opt/netconfig/data/pending.json` so they are rolled back after a restart |
| `app/index.html` | Web UI (ES/EN) |
| `control/` | opkg metadata: `control`, `openrc`, `schema.json` (`iframe_port: 8097`), `postinst`, `prerm`, `postrm` (using `/usr/lib/smhub/service-helpers.sh`) |
| `build.py` | Builds the `.ipk` with the Python standard library |
| `feed-index.py` | Rebuilds the local opkg feed index (`Packages`, `Packages.gz`) with every package in the feed, so other local apps stay listed |
| `install-root.sh` | Installs the package through a local opkg feed (`/opt/localfeed`) and restarts `smhub-services` so the app is registered in the panel |

API:

| Method | Path | Description |
|---|---|---|
| GET | `/api/state` | Interfaces, profiles, Wi-Fi state and pending change |
| POST | `/api/ipv4` | Set DHCP or static IPv4 on a profile |
| POST | `/api/wifi/radio` | Enable/disable Wi-Fi |
| GET | `/api/wifi/scan` | Nearby Wi-Fi networks |
| POST | `/api/wifi/connect` | Connect to a Wi-Fi network |
| POST | `/api/confirm`, `/api/revert` | Confirm or roll back the pending change |
| GET | `/api/smhub.json[?password=1]` | Current configuration in `smhub.json` format |

Wi-Fi is disabled by turning off autoconnect and disconnecting the device, not with `nmcli radio wifi off`: on the SMHUB Nano, blocking the radio via rfkill leaves the `aic8800` Wi-Fi driver unable to scan until a cold power cycle.

## Install on a SMHUB

From the hub web console (or SSH), with this repository copied to `~/smhub-netconfig`:

```bash
cd ~/smhub-netconfig
python3 build.py
sudo sh install-root.sh
```

Reload the panel: **netconfig** appears under Apps and in the sidebar. It can also be opened at `http://<hub-ip>:8097`.

## Uninstall

```bash
sudo opkg remove netconfig
```

If no other app uses the local feed, also remove it:

```bash
sudo sed -i '\#file:///opt/localfeed#d' /etc/opkg/smlight.conf
sudo rc-service smhub-services restart
```

## Notes

- This is not an official SMLIGHT app. The app format was worked out by inspecting SMHUB OS 1.0.2 and may change in future releases.
- Changing the network from a remote session can lock you out: keep the confirmation window in mind and, if in doubt, test from a device on the same LAN.

## License

MIT, see [LICENSE](LICENSE).
