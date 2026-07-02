"""
Phase 5a — Production Gateway Service
=======================================
Reads Modbus registers from the INVT CHF100A VFD, buffers readings to a local
SQLite outbox, and forwards them to the cloud API via HTTPS POST.

Architecture (ADR-006):
    Modbus Poller → SQLite outbox buffer → HTTPS Forwarder → POST /data

This file extends the working Modbus reader in logger.py into a full
store-and-forward pipeline with:
  - Config-driven operation (no hardcoded values — everything in config.json)
  - SQLite outbox for resilience during network outages
  - JWT token management with auto-renewal before expiry
  - Structured logging to console (INFO) and rotating log file (DEBUG)
  - Crash-resilient main loop — recovers from all expected error conditions

Run manually on Pi to verify:
    python3 gateway_service.py

Run as a managed service (start on boot, auto-restart on crash):
    sudo cp gateway.service /etc/systemd/system/
    sudo systemctl enable --now gateway

See README.md for full deployment instructions.
Hardware context: ADR-007 in docs/architecture-decisions.md.
Transport rationale: ADR-006 in docs/architecture-decisions.md.
"""

import json
import logging
import logging.handlers
import os
import sqlite3
import time
import traceback
from datetime import datetime, timedelta

import requests
from pymodbus.client import ModbusSerialClient


# ---------------------------------------------------------------------------
# COMPONENT 6 — Logging
# Set up before anything else so every failure at startup is recorded.
# ---------------------------------------------------------------------------

# Place gateway.log beside this script so it works from any working directory.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(_SCRIPT_DIR, "gateway.log")

log = logging.getLogger("gateway")
log.setLevel(logging.DEBUG)   # capture everything; handlers filter by level

# Console handler — INFO and above so the terminal is readable without noise
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)
log.addHandler(_console_handler)

# Rotating file handler — DEBUG level so post-mortem analysis has full detail
# 5 MB × 3 backups = up to ~15 MB of history before oldest is dropped
_file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)
log.addHandler(_file_handler)


# ---------------------------------------------------------------------------
# COMPONENT 1 — Config loader
# ---------------------------------------------------------------------------

_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "config.json")

# Required keys — startup fails with a clear error if any are missing.
_REQUIRED_CONFIG_KEYS = [
    "api_base_url",
    "api_username",
    "api_password",
    "modbus_port",
    "modbus_baudrate",
    "modbus_slave_id",
    "polling_interval_seconds",
    "component_instance_id",
    "company_id",
    "tag_map",
]


def load_config() -> dict:
    """Load and validate config.json.

    Raises FileNotFoundError if config.json is absent (tell the user to
    copy config.json.example and fill in real values).
    Raises ValueError if any required key is missing.
    Never logs credentials.
    """
    if not os.path.exists(_CONFIG_PATH):
        raise FileNotFoundError(
            "config.json not found at {path}.  "
            "Copy config.json.example → config.json and fill in real "
            "credentials and DB IDs.".format(path=_CONFIG_PATH)
        )

    with open(_CONFIG_PATH) as f:
        config = json.load(f)

    missing = [k for k in _REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        raise ValueError(
            "config.json is missing required keys: {}".format(missing)
        )

    # Log non-sensitive config values so startup is auditable.
    log.info(
        "Config loaded — api_base_url=%s  port=%s  baud=%d  slave=%d  "
        "poll=%ds  component_instance_id=%d  company_id=%d",
        config["api_base_url"],
        config["modbus_port"],
        config["modbus_baudrate"],
        config["modbus_slave_id"],
        config["polling_interval_seconds"],
        config["component_instance_id"],
        config["company_id"],
    )
    return config


# ---------------------------------------------------------------------------
# COMPONENT 2 — SQLite outbox buffer
# ---------------------------------------------------------------------------

_OUTBOX_DB = os.path.join(_SCRIPT_DIR, "outbox.db")

# Readings that have failed this many times are logged as permanently failed
# and never retried — prevents infinite retry of a corrupt or rejected payload.
MAX_RETRIES = 10


def init_outbox():
    """Create the outbox table if it does not already exist.

    Uses CREATE TABLE IF NOT EXISTS — safe to call on an existing database.
    """
    conn = sqlite3.connect(_OUTBOX_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outbox (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            payload     TEXT    NOT NULL,       -- JSON string of one /data payload
            created_at  TEXT    NOT NULL,       -- UTC ISO timestamp when recorded
            sent_at     TEXT,                   -- NULL until HTTP 201 received
            retry_count INTEGER DEFAULT 0       -- incremented on each failed attempt
        )
    """)
    conn.commit()
    conn.close()
    log.debug("Outbox ready at %s", _OUTBOX_DB)


def write_to_outbox(payload_dict: dict):
    """Persist one reading to the local outbox BEFORE any network attempt.

    Every reading is stored locally first so a network failure at send time
    never loses data — the forwarder picks it up on the next cycle.
    """
    payload_json = json.dumps(payload_dict)
    created_at = datetime.utcnow().isoformat()

    conn = sqlite3.connect(_OUTBOX_DB)
    conn.execute(
        "INSERT INTO outbox (payload, created_at) VALUES (?, ?)",
        (payload_json, created_at),
    )
    conn.commit()
    conn.close()

    log.debug(
        "Outbox write: tag_definition_id=%s  value_num=%s",
        payload_dict.get("tag_definition_id"),
        payload_dict.get("value_num"),
    )


def mark_sent(row_id: int):
    """Record successful delivery by setting sent_at to the current UTC time."""
    conn = sqlite3.connect(_OUTBOX_DB)
    conn.execute(
        "UPDATE outbox SET sent_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), row_id),
    )
    conn.commit()
    conn.close()


def increment_retry(row_id: int):
    """Increment retry_count for a row that failed to send."""
    conn = sqlite3.connect(_OUTBOX_DB)
    conn.execute(
        "UPDATE outbox SET retry_count = retry_count + 1 WHERE id = ?",
        (row_id,),
    )
    conn.commit()
    conn.close()


def get_retry_count(row_id: int) -> int:
    """Return the current retry_count for a row (used after increment)."""
    conn = sqlite3.connect(_OUTBOX_DB)
    row = conn.execute(
        "SELECT retry_count FROM outbox WHERE id = ?", (row_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def fetch_unsent() -> list:
    """Return unsent rows eligible for retry, ordered oldest-first.

    Rows with retry_count >= MAX_RETRIES are excluded — they have been
    permanently failed and logged.

    Returns:
        List of (id, payload_json) tuples.
    """
    conn = sqlite3.connect(_OUTBOX_DB)
    rows = conn.execute(
        "SELECT id, payload FROM outbox "
        "WHERE sent_at IS NULL AND retry_count < ? "
        "ORDER BY created_at ASC",
        (MAX_RETRIES,),
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# COMPONENT 3 — JWT token manager
# ---------------------------------------------------------------------------

class TokenManager:
    """Manages the JWT Bearer token for cloud API authentication.

    Handles login, token caching, expiry detection, and forced re-login
    on 401 responses — so the forwarder never needs to know about auth state.

    The server issues tokens that expire in 60 minutes (Phase 4d setting).
    We treat tokens as expired after 55 minutes to avoid using a token that
    expires in flight.

    Credentials are read from config at construction time.
    They are never written to logs or exception messages.
    """

    _TOKEN_LIFETIME_MINUTES = 55  # server expiry is 60 min; renew 5 min early

    def __init__(self, api_base_url: str, username: str, password: str):
        # Trailing slash on base URL would produce double-slash — strip it.
        self._login_url = api_base_url.rstrip("/") + "/login"
        self._username = username
        self._password = password   # stored in memory only; never logged
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def _is_valid(self) -> bool:
        """Return True if we hold a non-expired token."""
        if self._token is None or self._expires_at is None:
            return False
        return datetime.utcnow() < self._expires_at

    def login(self):
        """POST /login with credentials and cache the returned access_token.

        Raises requests.exceptions.RequestException on network or HTTP error.
        The exception message from requests does not contain credentials.
        """
        log.info("Authenticating with API (username: %s)", self._username)

        # requests.post with json= serialises the body and sets Content-Type.
        resp = requests.post(
            self._login_url,
            json={"username": self._username, "password": self._password},
            timeout=10,
        )
        resp.raise_for_status()  # raises HTTPError on 4xx / 5xx

        self._token = resp.json()["access_token"]
        self._expires_at = (
            datetime.utcnow() + timedelta(minutes=self._TOKEN_LIFETIME_MINUTES)
        )
        log.info(
            "Authentication successful — token valid for %d minutes",
            self._TOKEN_LIFETIME_MINUTES,
        )

    def get_token(self) -> str:
        """Return a valid token, logging in first if the current one is stale."""
        if not self._is_valid():
            self.login()
        return self._token

    def on_401(self):
        """Invalidate the cached token so the next get_token() triggers login.

        Called when the server returns 401 — the token may have been revoked
        server-side before the local expiry we calculated.
        """
        log.warning("Received 401 from API — will re-authenticate on next request")
        self._token = None
        self._expires_at = None


# ---------------------------------------------------------------------------
# COMPONENT 4 — HTTPS forwarder
# ---------------------------------------------------------------------------

def forward_outbox(token_manager: TokenManager, api_base_url: str):
    """Send all pending outbox rows to POST /data on the cloud API.

    Processing order: oldest rows first (created_at ASC) so data arrives in
    chronological order even after a backlog accumulates during an outage.

    Behaviour on each response code:
    - 201 Created    → mark_sent(), continue to next row
    - 401 Unauthorized → on_401() + one retry; if retry also fails, increment
                         retry_count and continue to the next row
    - Any other 4xx/5xx → increment retry_count, log warning, continue
    - ConnectionError/Timeout → log warning, STOP this cycle (network is down;
                                no point trying more rows until next poll)
    """
    url = api_base_url.rstrip("/") + "/data"
    rows = fetch_unsent()

    if not rows:
        log.debug("Outbox is empty — nothing to forward")
        return

    log.debug("Forwarding %d pending outbox row(s)", len(rows))

    for row_id, payload_json in rows:
        payload = json.loads(payload_json)

        # ── Attempt to send this row ────────────────────────────────────
        try:
            token = token_manager.get_token()
            resp = requests.post(
                url,
                json=payload,
                headers={"Authorization": "Bearer " + token},
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            # Network is unreachable — no point trying remaining rows.
            # All unsent rows stay in outbox and are retried next cycle.
            log.warning(
                "Network unreachable — stopping forward cycle, "
                "will retry at next poll"
            )
            return
        except requests.exceptions.Timeout:
            log.warning("Request timed out for outbox row %d", row_id)
            increment_retry(row_id)
            return   # also stop on timeout; may signal a wider network issue

        # ── Handle response ─────────────────────────────────────────────
        if resp.status_code == 201:
            mark_sent(row_id)
            log.info(
                "Sent outbox row %d — tag_definition_id=%s  value_num=%s",
                row_id,
                payload.get("tag_definition_id"),
                payload.get("value_num"),
            )

        elif resp.status_code == 401:
            # Token rejected — force re-login and retry this exact row once.
            token_manager.on_401()
            try:
                new_token = token_manager.get_token()
                resp2 = requests.post(
                    url,
                    json=payload,
                    headers={"Authorization": "Bearer " + new_token},
                    timeout=10,
                )
                if resp2.status_code == 201:
                    mark_sent(row_id)
                    log.info("Sent outbox row %d after re-authentication", row_id)
                else:
                    increment_retry(row_id)
                    log.warning(
                        "Row %d failed after re-authentication: HTTP %d — %s",
                        row_id, resp2.status_code, resp2.text[:200],
                    )
            except Exception as exc:
                increment_retry(row_id)
                log.warning(
                    "Row %d re-authentication attempt raised: %s", row_id, exc
                )

        else:
            # Unexpected status (e.g. 422 Unprocessable, 500 server error).
            increment_retry(row_id)
            retry_count = get_retry_count(row_id)
            if retry_count >= MAX_RETRIES:
                log.error(
                    "Row %d permanently failed after %d retries: "
                    "HTTP %d — %s",
                    row_id, MAX_RETRIES, resp.status_code, resp.text[:200],
                )
            else:
                log.warning(
                    "Row %d: HTTP %d — retry %d/%d",
                    row_id, resp.status_code, retry_count, MAX_RETRIES,
                )


# ---------------------------------------------------------------------------
# COMPONENT 5 — Modbus reader
# ---------------------------------------------------------------------------

# Register layout at address 0x3000 — identical to logger.py.
# Each entry is (register_name, scale_divisor).
# Dividing by 1 means the raw value is used unchanged.
REGISTER_MAP = [
    ("frequency",      100),   # registers[0] / 100 → Hz
    ("ref_frequency",  100),   # registers[1] / 100 → Hz  (often null in tag_map)
    ("dc_voltage",     10),    # registers[2] / 10  → V
    ("output_voltage", 1),     # registers[3] raw   → V
    ("current",        10),    # registers[4] / 10  → A
    ("rpm",            1),     # registers[5] raw   → RPM
    ("power",          10),    # registers[6] / 10  → kW
    ("torque",         10),    # registers[7] / 10  → %
]


def read_modbus(client: ModbusSerialClient, slave_id: int) -> dict | None:
    """Read 8 registers from the VFD and return decoded values.

    Uses the same address (0x3000), count (8), and scaling as logger.py so
    the two scripts produce identical numeric values.

    Returns:
        dict mapping register_name → float value, or None on Modbus error.
        The caller should skip the poll cycle and not write to outbox on None.
    """
    rr = client.read_holding_registers(
        address=0x3000,
        count=8,
        device_id=slave_id,
    )

    if rr.isError():
        log.warning("Modbus read error from slave %d: %s", slave_id, rr)
        return None

    values = {}
    for i, (name, divisor) in enumerate(REGISTER_MAP):
        values[name] = rr.registers[i] / divisor

    log.debug(
        "Modbus OK — freq=%.2f Hz  current=%.1f A  rpm=%d RPM  power=%.1f kW",
        values["frequency"],
        values["current"],
        int(values["rpm"]),
        values["power"],
    )
    return values


# ---------------------------------------------------------------------------
# COMPONENT 5 — Main polling loop
# ---------------------------------------------------------------------------

def run(config: dict):
    """Start the poll → buffer → forward loop.  Runs indefinitely.

    Every error inside the loop is caught and logged; the loop always
    continues so the systemd service never needs to restart due to a
    Python exception (restart is still available for hard crashes like OOM).

    Args:
        config: Validated dict from load_config().
    """
    poll_interval    = config["polling_interval_seconds"]
    modbus_port      = config["modbus_port"]
    modbus_baud      = config["modbus_baudrate"]
    slave_id         = config["modbus_slave_id"]
    component_id     = config["component_instance_id"]
    company_id       = config["company_id"]
    tag_map          = config["tag_map"]      # {register_name: tag_definition_id | null}
    api_base_url     = config["api_base_url"]

    # ModbusSerialClient parameters match logger.py exactly:
    # parity='N', stopbits=1, bytesize=8 are the VFD's factory RS485 settings.
    client = ModbusSerialClient(
        port=modbus_port,
        baudrate=modbus_baud,
        parity="N",
        stopbits=1,
        bytesize=8,
        timeout=1,
    )

    token_manager = TokenManager(
        api_base_url=api_base_url,
        username=config["api_username"],
        password=config["api_password"],
    )

    log.info(
        "Gateway starting up — port=%s  baud=%d  slave=%d  poll=%ds",
        modbus_port, modbus_baud, slave_id, poll_interval,
    )

    if not client.connect():
        # Can't open the serial port — USB adapter may not be attached.
        # Exit so systemd restarts after RestartSec (10 s in gateway.service).
        log.error(
            "Cannot open Modbus port %s — "
            "is the Waveshare USB adapter plugged in?",
            modbus_port,
        )
        raise SystemExit(1)

    log.info("Modbus connected on %s", modbus_port)

    while True:
        try:
            # ── Step 1: Read Modbus registers ───────────────────────────
            values = read_modbus(client, slave_id)

            if values is None:
                # Modbus read failed — skip this cycle.
                # Do NOT write to outbox: we have no valid data.
                log.warning("Modbus read failed — skipping poll cycle")
                time.sleep(poll_interval)
                continue

            # Timestamp in the format the /data endpoint expects.
            timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

            # ── Step 2: Write readings to outbox ────────────────────────
            # One row per tag.  Null tag_definition_id means the register
            # is read but not stored in the backend (e.g. ref_frequency).
            written = 0
            for register_name, tag_definition_id in tag_map.items():
                if tag_definition_id is None:
                    log.debug("Skipping %s — no tag_definition_id configured",
                              register_name)
                    continue

                if register_name not in values:
                    log.warning(
                        "tag_map key '%s' has no entry in REGISTER_MAP — skipping",
                        register_name,
                    )
                    continue

                # Note: the API uses value_num for numeric readings, not "value".
                # The DataCreate schema (schemas/telemetry.py) has value_num
                # (float) and value_text (str) — VFD readings are always numeric.
                payload = {
                    "timestamp":             timestamp,
                    "component_instance_id": component_id,
                    "tag_definition_id":     tag_definition_id,
                    "value_num":             values[register_name],
                    "company_id":            company_id,
                }
                write_to_outbox(payload)
                written += 1

            log.info("Poll complete — %d reading(s) written to outbox", written)

            # ── Step 3: Forward outbox to cloud ─────────────────────────
            forward_outbox(token_manager, api_base_url)

        except Exception:
            # Unexpected error (bug, not a handled condition).
            # Log full traceback, sleep 30 s, then resume the loop.
            log.error(
                "Unexpected error in poll loop:\n%s", traceback.format_exc()
            )
            time.sleep(30)
            continue

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as exc:
        log.critical("Cannot start: %s", exc)
        raise SystemExit(1)

    init_outbox()
    run(config)
