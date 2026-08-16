# Run modes

Every mode serves the same application on the same port. They differ in what the
server binds to, what it tells you, and how much it constrains you for safety.

```bash
./scripts/start.sh --mode lan
./scripts/start.sh --mode internet --port 8080
```

| Mode | Binds | Auth | Beta bypass | Reachable from |
|---|---|---|---|---|
| `local` | `127.0.0.1` | optional | allowed | this machine only |
| `lan` | `0.0.0.0` | recommended | allowed | your local network |
| `hotspot` | `0.0.0.0` | recommended | allowed | devices joined to your AP |
| `internet` | `0.0.0.0` | **forced** | **disabled** | anywhere |
| `vps` | `0.0.0.0` | **forced** | **disabled** | anywhere |

---

## `local`

Binds loopback and opens the GM screen in your default browser.

Click **Open Display Window**, drag the new window to your second monitor, press
`F11`. That window is the table view — map, tokens, and fog, no controls.

Nothing is reachable from other machines. This is the safest mode and the right
default for a group sitting around one table with one shared screen.

---

## `lan`

Binds all interfaces so other devices on your network can connect. The console
and the GM screen both show a **join URL and a QR code** — players point a phone
camera at the screen rather than typing an IP address.

Players open the join URL and land on `/play`: the fog-limited board, chat and
dice, their notes, and the vault.

**If players cannot connect:**

- Your host firewall is the usual culprit. Windows Defender Firewall prompts on
  first run — if you dismissed it, allow `python.exe` on **private** networks.
- Confirm everyone is on the same network and, on dual-band routers, ideally the
  same SSID. Guest networks are isolated from the main network by design.
- Some routers enable **AP isolation**, which blocks device-to-device traffic.
  It is usually a wireless setting labelled "AP isolation" or "client
  isolation".

---

## `hotspot`

Asks your operating system to turn your Wi-Fi adapter into an access point, then
serves EzVTT on it. For a table with no usable network.

> ### ⚠️ This mode frequently fails, and not because of EzVTT
>
> Creating a software access point has become steadily harder across all three
> platforms. Treat this mode as best-effort. **If it fails, EzVTT reports why and
> falls back to `lan`.**

**Platform reality:**

- **Windows** — the old `netsh wlan start hostednetwork` interface has been
  removed from most modern Wi-Fi drivers. Its replacement, Mobile Hotspot,
  requires adapter support and commonly refuses to start while you are connected
  to certain networks or to a VPN.
- **macOS** — Internet Sharing cannot be enabled reliably from the command line,
  and macOS will not share a Wi-Fi connection *over* Wi-Fi. Expect to enable it
  by hand in System Settings, if at all.
- **Linux** — works reasonably well given NetworkManager and an AP-capable
  adapter (`nmcli device wifi hotspot`). Many USB adapters are not AP-capable;
  check with `iw list | grep -A 10 "Supported interface modes"`.

**Even when the AP starts, client devices may still fail:**

- **Private / randomised Wi-Fi addresses** on modern phones can cause the device
  to appear as a new client on every reconnect.
- **Android and iOS detect that the hotspot has no internet access** and will
  often switch silently back to mobile data. Players may need to approve a "stay
  connected" prompt or disable automatic network switching.
- **Client isolation** on some adapters blocks joined devices from reaching your
  machine.
- **Managed laptops** — corporate and school policy usually blocks hotspot
  creation outright.

**The recommendation:** if any network exists at all — including one player's
phone hotspot that everyone joins — use `lan`. It is dramatically more reliable.

---

## `internet`

Serves EzVTT directly to the public internet from your own machine.

> ### ⚠️ Read before using
>
> - You must **forward a port** on your router to this machine. EzVTT prints the
>   address and port.
> - **Authentication is forced on and the beta bypass is disabled.** Not
>   configurable.
> - Traffic is **unencrypted HTTP** unless you put TLS in front of it.
>   Passwords and session cookies travel in clear text over plain HTTP.
> - Your home IP address becomes known to everyone you share it with.

For a one-off remote session this is workable. For anything recurring, use `vps`
mode behind a real certificate.

---

## `vps`

For always-on hosting behind a reverse proxy that terminates TLS. EzVTT trusts
`X-Forwarded-For` and `X-Forwarded-Proto`, and marks session cookies `Secure`.

> **Only enable this mode behind a proxy you control.** It trusts forwarded
> headers, which a direct-to-internet deployment must not do — a client could
> forge them.

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name vtt.example.com;

    ssl_certificate     /etc/letsencrypt/live/vtt.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vtt.example.com/privkey.pem;

    # Battlemaps are large. Raise this if uploads fail with 413.
    client_max_body_size 64M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for the live table sync.
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";

        # A quiet table should not be disconnected mid-session.
        proxy_read_timeout 3600s;
    }
}

server {
    listen 80;
    server_name vtt.example.com;
    return 301 https://$host$request_uri;
}
```

### systemd

```ini
[Unit]
Description=EzVTT
After=network.target

[Service]
Type=simple
User=ezvtt
WorkingDirectory=/opt/ezvtt
ExecStart=/opt/ezvtt/.venv/bin/python -m ezvtt --mode vps --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=5

# The application only ever needs to write inside its own data directory.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/ezvtt/data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now ezvtt
sudo journalctl -u ezvtt -f
```

Note that `ExecStart` binds `127.0.0.1` — nginx is the only thing that should
reach the application port directly.

---

## Backing up

Everything worth keeping is under `data/`. Stop the server, copy the directory,
restart. To reset entirely, delete `data/ezvtt.db`; the first-run admin wizard
reappears on next launch.
