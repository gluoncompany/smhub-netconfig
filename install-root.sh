#!/bin/sh
# Run as root: adds this app to the local opkg feed (/opt/localfeed), installs it and registers it in the panel.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
PKG=$(sed -n 's/^Package: *//p' "$HERE/control/control")
CONF=/etc/opkg/smlight.conf
mkdir -p /opt/localfeed
cp -f "$HERE"/feed/*.ipk /opt/localfeed/
python3 "$HERE/feed-index.py" /opt/localfeed
if ! grep -q 'file:///opt/localfeed' "$CONF"; then
    cp -n "$CONF" "$CONF.bak-localfeed" || true
    echo "src/gz local file:///opt/localfeed" >> "$CONF"
fi
opkg update 2>&1 | tail -3
opkg install --force-reinstall "$PKG" 2>&1 | tail -5
echo "Reiniciando smhub-services para registrar la app..."
rc-service smhub-services restart >/dev/null 2>&1 || true
sleep 3
rc-service "$PKG" status || true
echo INSTALL_ROOT_DONE
