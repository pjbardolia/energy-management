# Gateway Service — Deployment Guide

Reads INVT CHF100A VFD data over Modbus RTU and POSTs it to the cloud API.
Runs as a systemd service on the Raspberry Pi 3B+ at 192.168.0.200.

## Architecture

```
VFD (RS485) → Waveshare USB adapter → Pi (gateway_service.py) → HTTPS POST → Cloud API
                                             ↓
                                       SQLite outbox
                                   (store-and-forward buffer)
```

Readings are always written to `outbox.db` first. If the network is down,
they accumulate locally and are sent when connectivity is restored.

## Prerequisites

- Raspberry Pi 3B+ with Raspbian/Raspberry Pi OS
- Waveshare USB-to-4CH RS485/422 adapter plugged in (`/dev/ttyUSB0`)
- Python 3.9+ (pre-installed on Raspberry Pi OS)
- Network connectivity to the cloud API

## Setup

**1. Copy the gateway folder to the Pi**

```bash
scp -r gateway/ pi@192.168.0.200:/home/pi/gateway/
```

**2. Install Python dependencies**

```bash
ssh pi@192.168.0.200
cd /home/pi/gateway
pip3 install -r requirements_gateway.txt
```

**3. Configure credentials and IDs**

```bash
cp config.json.example config.json
nano config.json
```

Fill in:
- `api_username` and `api_password` — the account credentials for the cloud API
- `api_base_url` — the server address (default: `http://165.22.247.235:8001`)
- `component_instance_id` — the DB ID of the CHF100A component instance
- `company_id` — the DB ID of the company (tenant)
- `tag_map` — maps register names to `tag_definition_id` values in the DB

**4. Test manually**

```bash
python3 gateway_service.py
```

Watch the console output. You should see:
- "Config loaded" with the settings
- "Modbus connected on /dev/ttyUSB0"
- "Poll complete — 7 reading(s) written to outbox" every 10 seconds
- "Sent outbox row N — tag_definition_id=..." as readings are forwarded

Press Ctrl+C to stop.

**5. Install as systemd service**

```bash
sudo cp /home/pi/gateway/gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gateway
sudo systemctl start gateway
```

**6. Verify the service is running**

```bash
sudo systemctl status gateway
```

**7. Watch live logs**

```bash
journalctl -u gateway -f
```

Or read the rotating log file directly:

```bash
tail -f /home/pi/gateway/gateway.log
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Cannot open Modbus port" | USB adapter not plugged in | Plug in Waveshare adapter, restart service |
| "Network unreachable" | LAN/internet down | Readings buffer in outbox.db — sent when network returns |
| HTTP 401 errors | Wrong credentials or expired token | Check api_username/api_password in config.json |
| HTTP 422 errors | Wrong component_instance_id or company_id | Check IDs in config.json match the backend DB |
| Service won't start | config.json missing | Copy config.json.example → config.json and fill in values |

## Files

| File | Purpose |
|------|---------|
| `gateway_service.py` | Main service — Modbus reader + outbox + HTTPS forwarder |
| `config.json` | Runtime config — **not committed to git** (contains credentials) |
| `config.json.example` | Template with placeholder values — committed to git |
| `outbox.db` | SQLite buffer — created automatically, not committed to git |
| `gateway.log` | Rotating log file — created automatically, not committed to git |
| `gateway.service` | systemd unit file |
| `requirements_gateway.txt` | Python package list for fresh Pi setup |
| `logger.py` | Original working Modbus reader (reference, not for production) |
| `test.py` | One-shot register reader for debugging (reference only) |

## Outbox database

To inspect buffered readings directly:

```bash
sqlite3 /home/pi/gateway/outbox.db
sqlite> SELECT id, payload, sent_at, retry_count FROM outbox ORDER BY id DESC LIMIT 20;
```

Rows with `sent_at IS NOT NULL` have been successfully delivered.
Rows with `retry_count >= 10` have permanently failed and will not be retried.
