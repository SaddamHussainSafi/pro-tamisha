# Pro Trading scanner bridge

A small, standard-library-only Python program that runs on the Windows PC attached to the office
scanner (Toshiba e-STUDIO 409CS via NAPS2). Chrome cannot talk to a scanner by itself; the Pro
Trading web app asks this bridge on `http://127.0.0.1:47615` to scan, and the bridge uploads the
resulting PDF straight to the API. Nothing scanned ever changes stock, orders or invoices.

> Device integration is **unverified until the office proof-of-concept**. The `fake` driver is used
> for development and tests.

## Requirements
- Python 3.12+ (no third-party packages).
- NAPS2 installed (default `C:\Program Files\NAPS2\NAPS2.Console.exe`) with a scanner profile.

## Pair (once per PC)
1. In Pro Trading: **Scan Center → Scanners → Add a scanner PC**. Copy the command shown.
2. On the scanner PC:
   ```
   set PT_API_URL=https://erp.example.test
   python -m pro_trading_bridge pair ABCD-EFGH
   ```
   The code is single-use and expires after 10 minutes. The bridge stores its device token in
   `%USERPROFILE%\.pro-trading-bridge.json` (owner-only permissions) and prints a confirmation code.
3. Back in Pro Trading, click **Approve** on the scanner and type the confirmation code. Until then
   the bridge cannot scan.

## Run
```
set PT_API_URL=https://erp.example.test
set PT_ALLOWED_ORIGINS=https://erp.example.test
python -m pro_trading_bridge run
```
Install it as a logon task so it starts with Windows. It sends a heartbeat every 30 s so the web app
shows the scanner as Online / Not found / Driver missing.

| Variable | Default | Meaning |
|---|---|---|
| `PT_API_URL` | `http://127.0.0.1:8000` | Pro Trading API base URL |
| `PT_ALLOWED_ORIGINS` | — (required for `run`) | Comma list of web origins allowed to call the bridge |
| `PT_BRIDGE_PORT` | `47615` | Localhost port (the web app uses `VITE_SCAN_BRIDGE_URL`) |
| `PT_BRIDGE_STATE` | `~/.pro-trading-bridge.json` | Token file |
| `PT_SCAN_DRIVER` | `naps2` | `naps2` or `fake` |
| `PT_FAKE_MODE` | `ok` | Fake driver: `ok`, `unplugged`, `driver_missing`, `timeout`, `empty` |
| `PT_NAPS2_EXE` | NAPS2 default path | NAPS2 console executable |

## Security
- Binds **127.0.0.1 only**; rejects any `Host` other than `127.0.0.1:<port>`/`localhost:<port>`
  (DNS rebinding) and any `Origin` not in `PT_ALLOWED_ORIGINS`.
- A web page cannot scan on its own: each scan needs a server-signed, single-use capability valid for
  5 minutes, issued only to a signed-in user with `scan.create` for that location and that device.
- One scan at a time (`409 BUSY`). NAPS2 is launched with an argument list (no shell) into a private
  temp directory that is deleted afterwards; output must be a real PDF.
- Revoking the scanner in Pro Trading disables the token immediately.

## Endpoints
- `GET /v1/status` → `{bridge_version, paired, device_id, scanner_state, scanner_detail}`
- `POST /v1/scan` `{job_id, capability}` → the job after upload (or `FAILED` with an error code).

## When things go wrong
| Scan Center shows | Do this |
|---|---|
| "Can't reach the scanner PC" | Start the bridge on the scanner PC, or use **Upload a file**. |
| Scanner not found | Check the USB/network cable and power, then scan again. |
| Driver missing | Reinstall NAPS2 / the scanner driver. |
| Took too long | Clear any paper jam, then scan again. |
| Scanner revoked | Pair again as a new scanner. |
