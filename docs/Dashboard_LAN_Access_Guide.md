# Dashboard LAN Access Guide

How to view the dashboard (`mpu/frontend/`, served by `mpu/main.py`) from
the host machine's browser and from a phone on the same Wi-Fi, when
`mpu/main.py` is run inside WSL2.

## Run the server

```
PYTHONPATH=mpu/common:mpu/ingestion:mpu/registry:mpu/pipeline:mpu/history:mpu/api:mpu/monitoring \
    python3 mpu/main.py --mqtt-host localhost
```

Binds to `0.0.0.0:8080` by default (REST + WebSocket + static frontend,
one port).

## View on the host machine (Windows, from WSL2)

Windows' localhost forwarding proxies `127.0.0.1` into the WSL2 VM
automatically, so just open:

```
http://localhost:8080
```

## View on a phone (same Wi-Fi)

WSL2's localhost forwarding does **not** cover traffic arriving on a
LAN-facing Windows interface (e.g. Ethernet/Wi-Fi), so one manual
port-forward from Windows to the WSL2 VM is required.

1. Get the WSL2 VM's current IP (changes on reboot):
   ```
   wsl hostname -I
   ```
2. In an **Administrator PowerShell** on Windows, forward the port
   (replace the IP with the output of step 1):
   ```powershell
   netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=<WSL_IP>
   ```
3. Allow it through Windows Firewall (one-time):
   ```powershell
   New-NetFirewallRule -DisplayName "MPU Dashboard 8080" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow
   ```
4. Find the host machine's LAN IPv4 address (the adapter actually
   connected to the router, e.g. `Ethernet` or `Wi-Fi`):
   ```powershell
   ipconfig
   ```
5. On the phone, browse to:
   ```
   http://<host-LAN-IP>:8080
   ```

## Notes

- The WSL2 VM IP can change across reboots. If the phone can no longer
  connect, re-run step 2 above with the new IP from `wsl hostname -I`.
- To remove the port-forward rule later:
  ```powershell
  netsh interface portproxy delete v4tov4 listenport=8080 listenaddress=0.0.0.0
  ```
