#!/usr/bin/env python3
# Rebuilds Packages / Packages.gz for every .ipk in a feed folder (keeps only the newest version of each package).
import gzip, hashlib, io, os, sys, tarfile
feed = sys.argv[1] if len(sys.argv) > 1 else "/opt/localfeed"

def control_of(path):
    data = open(path, "rb").read()
    pos = 8
    while pos < len(data):
        name = data[pos:pos + 16].decode().strip().rstrip("/")
        size = int(data[pos + 48:pos + 58].decode().strip())
        body = data[pos + 60:pos + 60 + size]
        if name.startswith("control.tar"):
            with tarfile.open(fileobj=io.BytesIO(body)) as tf:
                for m in tf.getmembers():
                    if m.name.lstrip("./") == "control":
                        return tf.extractfile(m).read().decode().strip(), data
        pos += 60 + size + (size % 2)
    raise ValueError("no control in " + path)

def vkey(v):
    return [int(x) if x.isdigit() else x for x in v.replace("-", ".").split(".")]

best = {}
for f in sorted(os.listdir(feed)):
    if not f.endswith(".ipk"):
        continue
    ctrl, data = control_of(os.path.join(feed, f))
    fields = dict(l.split(":", 1) for l in ctrl.splitlines() if ":" in l and not l.startswith(" "))
    pkg, ver = fields["Package"].strip(), fields["Version"].strip()
    if pkg not in best or vkey(ver) > vkey(best[pkg][1]):
        best[pkg] = (f, ver, ctrl, data)
entries = []
for pkg, (f, ver, ctrl, data) in sorted(best.items()):
    entries.append("%s\nFilename: %s\nSize: %d\nSHA256sum: %s\n" % (ctrl, f, len(data), hashlib.sha256(data).hexdigest()))
    print("  %s %s" % (pkg, ver))
text = "\n".join(entries) + "\n"
open(os.path.join(feed, "Packages"), "w").write(text)
with gzip.open(os.path.join(feed, "Packages.gz"), "wb") as g:
    g.write(text.encode())
