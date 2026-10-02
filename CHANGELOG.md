# Changelog

## 1.0.4
- Unmanaged Wi-Fi interfaces (e.g. `ap0` from the wifi-ap app) are ignored, so turning Wi-Fi off only disconnects the client connection and never touches the access point.

## 1.0.3
- After a rollback, a banner links back to the previous IP address.
- Layout: the Wi-Fi connect and `smhub.json` export cards sit in the same grid as the interfaces.
- The Wi-Fi on/off button moved into the Wi-Fi interface card; Wi-Fi profiles are shown even when disconnected.

## 1.0.2
- The pending-change banner with the link to the new address appears immediately, before the connection drops (changes are applied 3 s later).
- Profiles in modes other than DHCP/static (e.g. `usb0` shared) are read-only.

## 1.0.1
- Wi-Fi on/off uses autoconnect + disconnect instead of rfkill (`aic8800` driver issue).

## 1.0.0
- First release: static IP/DHCP per interface with automatic rollback, Wi-Fi scan/connect, `smhub.json` export, ES/EN UI.
