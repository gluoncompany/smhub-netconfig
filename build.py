#!/usr/bin/env python3
# Builds an SMHUB app .ipk from app/ (installed to /opt/<package>/) and control/. Output: feed/<pkg>_<ver>_all.ipk
import io, os, tarfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
EXEC = {"postinst", "prerm", "postrm", "openrc", "server.py"}

def targz(entries):
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        dirs = set()
        for arc, src in entries:
            parts = arc.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                if d not in dirs and d != ".":
                    dirs.add(d)
                    ti = tarfile.TarInfo(d + "/"); ti.type = tarfile.DIRTYPE; ti.mode = 0o755; ti.mtime = int(time.time())
                    tf.addfile(ti)
            data = open(src, "rb").read()
            ti = tarfile.TarInfo(arc); ti.size = len(data); ti.mtime = int(time.time())
            ti.mode = 0o755 if os.path.basename(arc) in EXEC else 0o644
            tf.addfile(ti, io.BytesIO(data))
    return bio.getvalue()

def ar(members):
    out = b"!<arch>\n"
    for name, data in members:
        hdr = "%-16s%-12d%-6d%-6d%-8s%-10d`\n" % (name + "/", int(time.time()), 0, 0, "100644", len(data))
        out += hdr.encode() + data + (b"\n" if len(data) % 2 else b"")
    return out

ctrl_dir = os.path.join(HERE, "control"); app_dir = os.path.join(HERE, "app")
fields = dict(l.split(":", 1) for l in open(os.path.join(ctrl_dir, "control")).read().strip().splitlines() if ":" in l)
pkg, ver = fields["Package"].strip(), fields["Version"].strip()
control = targz([("./" + f, os.path.join(ctrl_dir, f)) for f in sorted(os.listdir(ctrl_dir))])
files = sorted(f for f in os.listdir(app_dir) if os.path.isfile(os.path.join(app_dir, f)) and not f.endswith(".pyc"))
data = targz([("./opt/%s/%s" % (pkg, f), os.path.join(app_dir, f)) for f in files])
ipk = ar([("debian-binary", b"2.0\n"), ("control.tar.gz", control), ("data.tar.gz", data)])
os.makedirs(os.path.join(HERE, "feed"), exist_ok=True)
name = "%s_%s_all.ipk" % (pkg, ver)
open(os.path.join(HERE, "feed", name), "wb").write(ipk)
print("OK", name, len(ipk))
