"""
Phase 5a — Production Gateway Service (multi-device)
======================================================
Polls 14 VFDs across three models on a single RS485 bus, buffers every reading
to a local SQLite outbox, and forwards them to the cloud API via HTTPS POST.

Architecture (ADR-006):
    Modbus Poller → SQLite outbox buffer → HTTPS Forwarder → POST /data

Supported VFD models (register maps are defined in VFD_REGISTER_MAPS below —
they are hardware specs, not deployment config, so they live in code):
  - INVT_CHF100A  : 8 registers at 0x3000
  - YASKAWA_A1000 : 36 registers at 0x0023 (idx 1=Hz, 2=V, 3=A, 4=kW, 35=Vdc)
  - YASKAWA_V1000 : same spec as A1000
  - YASKAWA_F7    : same spec as A1000

Device roster (which slave ID maps to which model and component_instance_id)
and all RS485/API parameters live in config.json — no code changes are needed
to add, remove, or reconfigure a device.

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
from datetime import datetime, timedelta, timezone

import requests
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusIOException


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
# VFD register maps — hardware specs, defined once here in code.
#
# Each entry in "registers" is a tuple:
#   (tag_name, register_index, divisor)
#
#   tag_name      : str  — key to look up in config["tag_definition_ids"] to
#                          get the backend tag_definition_id for this reading.
#                   None — the register is read from the device (as part of a
#                          single block read) but is never stored in the DB.
#                          Using None avoids two block reads where one suffices.
#
#   register_index: int  — 0-based position in the response register array.
#
#   divisor       : int  — raw value is divided by this to get the engineering
#                          value (e.g. raw 5000 / 100 = 50.00 Hz).
#                          Divisor of 1 means the raw value is used unchanged.
# ---------------------------------------------------------------------------

# INVT CHF100A — 8 consecutive registers starting at 0x3000.
# Source: INVT CHF100A communications manual, parameter group F7.
_INVT_CHF100A_SPEC = {
    "address": 0x3000,
    "count": 8,
    "registers": [
        ("frequency",      0, 100),  # Output frequency  / 100 → Hz
        (None,             1, 100),  # Reference frequency — read but not stored
        ("dc_voltage",     2, 10),   # DC bus voltage    / 10  → V
        ("output_voltage", 3, 1),    # Output voltage    raw   → V
        ("current",        4, 10),   # Output current    / 10  → A
        ("rpm",            5, 1),    # Motor speed       raw   → RPM
        ("power",          6, 100),  # Output power      / 100 → kW  (verified 2026-07-16: raw×0.01=kW)
        ("torque",         7, 10),   # Output torque     / 10  → %
    ],
}

# Yaskawa A1000 / V1000 — 6 consecutive registers starting at 0x0023.
# Both models use the same register layout so they share this spec.
# Source: Yaskawa A1000 technical manual, U1-xx monitor parameters.
# Note: register [5] holds a speed/rpm value but it is not reliable on this
# installation — tag_name is None so it is read as part of the block but
# never stored in the database.
_YASKAWA_SPEC = {
    # Yaskawa MEMOBUS monitor registers — A1000 / V1000 / F7.
    #
    # *** Verified against the VFD front panel on 2026-07-12 (Jet 16, slave 3) ***
    # 0x0023 is the frequency REFERENCE (setpoint), NOT the output frequency.
    # The previous spec started here and mapped fields sequentially, which
    # shifted every label one register late:
    #   - output voltage (308.2 V) was reported as current (30.82 A)
    #   - output current (4.0 A)   was reported as power   (4.0 kW)
    # Frequency appeared correct only because at setpoint the reference
    # equals the output. Do NOT "simplify" this back to a contiguous
    # 5-register read from index 0 — the off-by-one returns silently.
    #
    # Single read of 36 registers (0x0023..0x0046); indices are offsets.
    #
    #   idx  addr    monitor  meaning              scale
    #    0   0x0023  U1-01    frequency reference  (not stored)
    #    1   0x0024  U1-02    output frequency     /100 -> Hz
    #    2   0x0025  U1-06    output voltage       /10  -> V
    #    3   0x0026  U1-03    output current       /10  -> A
    #    4   0x0027  U1-08    output power         /100 -> kW
    #   35   0x0046  U1-07    DC bus voltage       x1   -> V
    #
    # rpm and torque are NOT mapped. 0x0042 does not decode to either under
    # any scaling that matches the panel. Unmapped renders as an em-dash;
    # wrong renders as a plausible lie.
    # NOTE: the A1000 rejects reads longer than ~16 registers with Modbus
    # exception 3 (Illegal Data Value). Verified on the wire 2026-07-12:
    # count=12 succeeds, count=24 and count=36 both fail. dc_voltage (0x0046)
    # therefore CANNOT be folded into this block and needs its own read.
    # Do NOT "optimize" these back into a single transaction.
    "address": 0x0023,
    "count": 5,
    "registers": [
        ("frequency",      1, 100),
        ("output_voltage", 2,  10),
        ("current",        3,  10),
        ("power",          4, 100),
    ],
    "extra_reads": [
        {"address": 0x0046, "count": 1,
         "registers": [("dc_voltage", 0, 1)]},
    ],
}

# Master lookup: vfd_model string (as used in config.json) → register spec.
# YASKAWA_A1000 and YASKAWA_V1000 point to the same spec object — they are
# kept as separate model names so the device list is self-documenting.
VFD_REGISTER_MAPS = {
    "INVT_CHF100A":   _INVT_CHF100A_SPEC,
    "YASKAWA_A1000": _YASKAWA_SPEC,
    "YASKAWA_V1000": _YASKAWA_SPEC,   # same layout as A1000
    "YASKAWA_F7":    _YASKAWA_SPEC,
}


# ---------------------------------------------------------------------------
# COMPONENT 1 — Config loader
# ---------------------------------------------------------------------------

_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "config.json")

# Top-level keys the service cannot start without.
_REQUIRED_TOP_KEYS = [
    "api_base_url",
    "api_username",
    "api_password",
    "modbus_port",
    "modbus_baudrate",
    "polling_interval_seconds",
    "company_id",
    "tag_definition_ids",   # {tag_name: tag_definition_id} — same across all devices
    "devices",              # list of device dicts
]

# Keys required inside each device entry in the "devices" list.
_REQUIRED_DEVICE_KEYS = ["name", "slave_id", "vfd_model", "component_instance_id"]


def load_config() -> dict:
    """Load and validate config.json.

    Raises FileNotFoundError if config.json is absent.
    Raises ValueError if required keys are missing or any device entry is
    malformed or references an unknown vfd_model.
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

    # Check top-level required keys.
    missing = [k for k in _REQUIRED_TOP_KEYS if k not in config]
    if missing:
        raise ValueError("config.json is missing required keys: {}".format(missing))

    # Validate tag_definition_ids is a non-empty dict.
    if not isinstance(config["tag_definition_ids"], dict) or not config["tag_definition_ids"]:
        raise ValueError("config.json: 'tag_definition_ids' must be a non-empty dict.")

    # Validate devices list.
    devices = config["devices"]
    if not isinstance(devices, list) or len(devices) == 0:
        raise ValueError("config.json: 'devices' must be a non-empty list.")

    for i, device in enumerate(devices):
        # Check each device has all required keys.
        missing_dev = [k for k in _REQUIRED_DEVICE_KEYS if k not in device]
        if missing_dev:
            raise ValueError(
                "config.json: device[{}] ({}) is missing keys: {}".format(
                    i, device.get("name", "?"), missing_dev
                )
            )
        # Check the vfd_model is one we know how to talk to.
        if device["vfd_model"] not in VFD_REGISTER_MAPS:
            raise ValueError(
                "config.json: device '{}' has unknown vfd_model '{}'. "
                "Known models: {}".format(
                    device["name"],
                    device["vfd_model"],
                    list(VFD_REGISTER_MAPS.keys()),
                )
            )

    # Log non-sensitive config values so startup is auditable.
    log.info(
        "Config loaded — api_base_url=%s  port=%s  baud=%d  poll=%ds  "
        "%d device(s) configured",
        config["api_base_url"],
        config["modbus_port"],
        config["modbus_baudrate"],
        config["polling_interval_seconds"],
        len(devices),
    )
    for device in devices:
        log.info(
            "  Device: %-8s  slave=%2d  model=%-16s  component_instance_id=%d",
            device["name"],
            device["slave_id"],
            device["vfd_model"],
            device["component_instance_id"],
        )

    return config


# ---------------------------------------------------------------------------
# COMPONENT 2 — SQLite outbox buffer
# ---------------------------------------------------------------------------

_OUTBOX_DB = os.path.join(_SCRIPT_DIR, "outbox.db")

# Readings that have failed this many times are logged as permanently failed
# and never retried — prevents infinite retry of a corrupt or rejected payload.
MAX_RETRIES = 10

# Number of outbox rows bundled into a single POST /data/batch request.
# At ~340 ms round-trip, 200 rows per request raises throughput from
# ~2.9 rows/s (one POST per row) to 500+ rows/s.
BATCH_SIZE = 200


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
        "Outbox write: component_instance_id=%s  tag_definition_id=%s  value_num=%s",
        payload_dict.get("component_instance_id"),
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


def fetch_unsent(limit: int = 0) -> list:
    """Return unsent rows eligible for retry, ordered oldest-first (by id).

    Rows with retry_count >= MAX_RETRIES are excluded — they have been
    permanently failed and logged.

    Args:
        limit: Maximum rows to return.  0 (default) returns all unsent rows.
               Pass BATCH_SIZE to cap a single forward cycle.

    Returns:
        List of (id, payload_json) tuples.
    """
    conn = sqlite3.connect(_OUTBOX_DB)
    if limit > 0:
        rows = conn.execute(
            "SELECT id, payload FROM outbox "
            "WHERE sent_at IS NULL AND retry_count < ? "
            "ORDER BY id ASC LIMIT ?",
            (MAX_RETRIES, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, payload FROM outbox "
            "WHERE sent_at IS NULL AND retry_count < ? "
            "ORDER BY id ASC",
            (MAX_RETRIES,),
        ).fetchall()
    conn.close()
    return rows


def count_unsent() -> int:
    """Return the number of outbox rows still waiting to be sent.

    Used after a successful batch flush to log the remaining backlog.
    That single number is the health signal: if it climbs, the forwarder
    is falling behind.
    """
    conn = sqlite3.connect(_OUTBOX_DB)
    row = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL AND retry_count < ?",
        (MAX_RETRIES,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def mark_batch_sent(row_ids: list):
    """Mark a batch of rows sent in a single UPDATE inside one transaction.

    Only called after the server returns 2xx — if the process dies between
    the HTTP response and this UPDATE, the rows stay unsent and are re-sent
    on the next cycle (duplicates are far better than lost data).
    """
    if not row_ids:
        return
    placeholders = ",".join("?" * len(row_ids))
    conn = sqlite3.connect(_OUTBOX_DB)
    conn.execute(
        "UPDATE outbox SET sent_at = ? WHERE id IN ({})".format(placeholders),
        # datetime.now(timezone.utc) is the non-deprecated replacement for utcnow()
        [datetime.now(timezone.utc).isoformat()] + list(row_ids),
    )
    conn.commit()
    conn.close()


def increment_retry_batch(row_ids: list):
    """Increment retry_count for a batch of rows in a single UPDATE.

    Used only for 4xx responses on single-row fallback sends — never on a
    full 200-row batch, to avoid silently expiring the entire backlog.
    """
    if not row_ids:
        return
    placeholders = ",".join("?" * len(row_ids))
    conn = sqlite3.connect(_OUTBOX_DB)
    conn.execute(
        "UPDATE outbox SET retry_count = retry_count + 1 WHERE id IN ({})".format(
            placeholders
        ),
        list(row_ids),
    )
    conn.commit()
    conn.close()


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

def _fallback_send_one_by_one(
    row_ids: list,
    readings: list,
    token_manager: TokenManager,
    api_base_url: str,
):
    """Send each row individually after a batch 4xx to isolate the bad row.

    A 4xx on a 200-row batch does not mean all 200 rows are bad — one corrupt
    row can poison the whole payload.  Incrementing retry_count on all 200
    would silently expire the entire backlog after MAX_RETRIES cycles.

    This function re-sends each row as a single-item batch.  Good rows are
    marked sent normally; only the genuinely bad row gets its retry_count
    incremented.  This path is slow (one round-trip per row) but only runs
    on error, so it does not affect steady-state throughput.
    """
    url = api_base_url.rstrip("/") + "/data/batch"
    for row_id, reading in zip(row_ids, readings):
        try:
            token = token_manager.get_token()
            resp = requests.post(
                url,
                json={"readings": [reading]},
                headers={"Authorization": "Bearer " + token},
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            log.warning(
                "Network unreachable during per-row fallback — stopping"
            )
            return
        except requests.exceptions.Timeout:
            log.warning(
                "Timeout during per-row fallback for row %d — stopping", row_id
            )
            return

        if resp.status_code in (200, 201, 202):
            mark_sent(row_id)

        elif resp.status_code == 401:
            token_manager.on_401()
            try:
                new_token = token_manager.get_token()
                resp2 = requests.post(
                    url,
                    json={"readings": [reading]},
                    headers={"Authorization": "Bearer " + new_token},
                    timeout=10,
                )
                if resp2.status_code in (200, 201, 202):
                    mark_sent(row_id)
                else:
                    increment_retry(row_id)
                    log.warning(
                        "Fallback row %d failed after re-auth: HTTP %d",
                        row_id, resp2.status_code,
                    )
            except Exception as exc:
                increment_retry(row_id)
                log.warning("Fallback row %d re-auth raised: %s", row_id, exc)

        elif 400 <= resp.status_code < 500:
            # This specific row is bad — charge only this row.
            increment_retry(row_id)
            log.warning(
                "Fallback row %d rejected: HTTP %d — %s",
                row_id, resp.status_code, resp.text[:100],
            )

        else:
            # 5xx — server problem, stop the loop; remaining rows stay unsent
            # and will be picked up in the next batch cycle.
            log.warning(
                "Fallback row %d: server error HTTP %d — stopping fallback, "
                "remaining rows will retry next cycle",
                row_id, resp.status_code,
            )
            return


def _forward_one_batch(token_manager: TokenManager, api_base_url: str) -> bool:
    """Send ONE batch of up to BATCH_SIZE rows to POST /data/batch.

    Returns True only if a batch was successfully flushed — the caller uses
    this to decide whether to keep draining. Any other outcome (empty outbox,
    network error, 4xx, 5xx) returns None/False, which stops the drain loop
    and defers to the next poll cycle.

    Behaviour per response code:
    - 2xx         → mark_batch_sent() in one UPDATE, log backlog count
    - 401         → on_401() + one retry; fall back to per-row on second failure
    - other 4xx   → _fallback_send_one_by_one() to isolate the bad row;
                    only that row gets its retry_count incremented
    - 5xx         → leave all rows unsent (server problem, payload is fine)
    - network err → leave all rows unsent, stop this cycle
    """
    url = api_base_url.rstrip("/") + "/data/batch"
    rows = fetch_unsent(limit=BATCH_SIZE)

    if not rows:
        log.debug("Outbox is empty — nothing to forward")
        return

    row_ids = [r[0] for r in rows]

    # Build the batch body.  Strip company_id — the API derives it from the JWT.
    # value_text defaults to None for VFD numeric readings not present in payload.
    readings = []
    for _, payload_json in rows:
        p = json.loads(payload_json)
        readings.append({
            "timestamp":             p["timestamp"],
            "component_instance_id": p["component_instance_id"],
            "tag_definition_id":     p["tag_definition_id"],
            "value_num":             p.get("value_num"),
            "value_text":            p.get("value_text"),
        })

    try:
        token = token_manager.get_token()
        resp = requests.post(
            url,
            json={"readings": readings},
            headers={"Authorization": "Bearer " + token},
            timeout=30,   # larger than single-row; 200 rows at ~340 ms RTT
        )
    except requests.exceptions.ConnectionError:
        log.warning(
            "Network unreachable — stopping forward cycle, will retry at next poll"
        )
        return
    except requests.exceptions.Timeout:
        log.warning(
            "Batch request timed out (%d rows) — will retry at next poll", len(rows)
        )
        return

    if resp.status_code in (200, 201, 202):
        mark_batch_sent(row_ids)
        remaining = count_unsent()
        log.info("Flushed %d rows (backlog: %d remaining)", len(rows), remaining)
        return True

    elif resp.status_code == 401:
        token_manager.on_401()
        try:
            new_token = token_manager.get_token()
            resp2 = requests.post(
                url,
                json={"readings": readings},
                headers={"Authorization": "Bearer " + new_token},
                timeout=30,
            )
            if resp2.status_code in (200, 201, 202):
                mark_batch_sent(row_ids)
                remaining = count_unsent()
                log.info(
                    "Flushed %d rows after re-auth (backlog: %d remaining)",
                    len(rows), remaining,
                )
                return True
            else:
                # Re-auth succeeded but the batch was still rejected.
                # Fall back to per-row to avoid blanket retry charges.
                log.warning(
                    "Batch of %d rows failed after re-auth: HTTP %d — "
                    "falling back to per-row send to isolate bad row",
                    len(rows), resp2.status_code,
                )
                _fallback_send_one_by_one(row_ids, readings, token_manager, api_base_url)
        except Exception as exc:
            log.warning(
                "Re-auth attempt raised: %s — batch of %d rows left unsent", exc, len(rows)
            )

    elif 400 <= resp.status_code < 500:
        # One bad row in the batch caused a 4xx.  Blanket-incrementing retry_count
        # on all 200 rows would silently expire the entire backlog after MAX_RETRIES
        # cycles — the blast radius is 200× that of the old single-row forwarder.
        # Fall back to per-row sends so only the offending row gets charged.
        log.warning(
            "Batch rejected (HTTP %d) — falling back to per-row send to isolate bad row. "
            "%s",
            resp.status_code, resp.text[:200],
        )
        _fallback_send_one_by_one(row_ids, readings, token_manager, api_base_url)

    else:
        # 5xx: server-side problem — the payload is fine, the server is not.
        # Leave all rows unsent and do NOT increment retry_count.
        log.warning(
            "Batch of %d rows: server error HTTP %d — leaving unsent, "
            "will retry at next poll",
            len(rows), resp.status_code,
        )


def forward_outbox(token_manager: TokenManager, api_base_url: str):
    """Drain the outbox, up to MAX_BATCHES_PER_CYCLE batches per poll cycle.

    Batching alone was not enough. _forward_one_batch() sends 200 rows in one
    request, but the poll loop only calls the forwarder once per cycle, and a
    cycle takes ~23 s (14 Modbus reads at 9600 baud, plus timeouts on any dead
    slave). That capped the drain at 200 rows / 23 s ≈ 8.7 rows/sec against a
    ~3.8 rows/sec production rate — barely positive, and hopeless against a
    132k-row backlog.

    Looping lifts the ceiling to 5,000 rows per cycle. Once the backlog is
    cleared the loop exits on the first empty fetch, so steady-state cost is
    one cheap SELECT per cycle.

    The MAX_BATCHES_PER_CYCLE cap matters: without it, a first run against a
    large backlog would block the poll loop for minutes and we would miss
    Modbus reads. Bounded, the worst case is ~25 s of flushing while catching
    up, and effectively zero once caught up.
    """
    MAX_BATCHES_PER_CYCLE = 25   # 25 × 200 = 5,000 rows/cycle ceiling

    for _ in range(MAX_BATCHES_PER_CYCLE):
        if not _forward_one_batch(token_manager, api_base_url):
            return   # empty, or an error the batch function already logged


def post_heartbeat(
    token_manager:    TokenManager,
    api_base_url:     str,
    poll_duration_sec: float,
    machines_polled:  int,
    machines_failed:  int,
) -> None:
    """
    POST /gateway/heartbeat after every poll cycle.
    Tells the server the Pi is alive and reports basic poll health.
    Failures are logged as warnings but never raise — a heartbeat failure
    must never interrupt the poll loop.
    """
    try:
        token = token_manager.get_token()
        resp  = requests.post(
            api_base_url.rstrip("/") + "/gateway/heartbeat",
            json = {
                "poll_duration_sec": round(poll_duration_sec, 2),
                "machines_polled":   machines_polled,
                "machines_failed":   machines_failed,
            },
            headers = {"Authorization": "Bearer " + token},
            timeout = 5,  # must never slow down the poll cycle
        )
        if resp.status_code == 204:
            log.debug(
                "Heartbeat posted (%.1fs poll, %d/%d machines ok)",
                poll_duration_sec,
                machines_polled - machines_failed,
                machines_polled,
            )
        elif resp.status_code == 401:
            token_manager.on_401()
            log.warning("Heartbeat 401 — token invalidated, will retry next cycle")
        else:
            log.warning("Heartbeat unexpected status %d", resp.status_code)
    except Exception as exc:
        # Fire-and-forget: any exception is logged but never propagated.
        log.warning("Heartbeat post failed: %s", exc)


# ---------------------------------------------------------------------------
# COMPONENT 5a — Modbus reader (single device)
# ---------------------------------------------------------------------------

def read_modbus(
    client: ModbusSerialClient,
    slave_id: int,
    vfd_model: str,
) -> dict | None:
    """Read registers from one VFD and return decoded tag values.

    Looks up the register spec for vfd_model from VFD_REGISTER_MAPS, reads
    the appropriate address and count, then applies each register's divisor
    to produce engineering-unit values.

    Args:
        client:    Open ModbusSerialClient (one shared connection for all devices
                   on the same RS485 bus).
        slave_id:  Modbus slave address of this specific device.
        vfd_model: Key into VFD_REGISTER_MAPS (e.g. "INVT_CHF100A").

    Returns:
        dict mapping tag_name → float value for all non-None named registers.
        Returns None on any Modbus error — caller should skip this device's
        readings for this cycle without writing to outbox.

    Error handling:
        ModbusIOException is caught here and logged as WARNING — it is a normal
        transient condition (CRC error, timeout, VFD powered off) that does not
        warrant a full traceback.  The isError() check below catches error
        response objects that pymodbus returns instead of raising.
    """
    spec = VFD_REGISTER_MAPS[vfd_model]
    address = spec["address"]
    count = spec["count"]

    try:
        rr = client.read_holding_registers(
            address=address,
            count=count,
            device_id=slave_id,
        )
    except ModbusIOException as exc:
        # Transient serial-level failure (CRC mismatch, no response, framing
        # error).  Log as WARNING — one bad read is expected occasionally.
        log.warning("Slave %d [%s]: Modbus read failed: %s", slave_id, vfd_model, exc)
        return None

    if rr.isError():
        # pymodbus can also signal errors as a response object rather than
        # raising an exception — this guard catches those cases.
        log.warning("Slave %d [%s]: Modbus error response: %s", slave_id, vfd_model, rr)
        return None

    # Decode registers using this model's spec.
    # Skip entries where tag_name is None — those registers are read as part
    # of the block but carry no value we store in the database.
    values = {}
    for tag_name, reg_index, divisor in spec["registers"]:
        if tag_name is None:
            continue
        values[tag_name] = rr.registers[reg_index] / divisor

    log.debug(
        "Slave %d [%s]: OK — freq=%.2f Hz  current=%.3f A  "
        "%d tag(s) decoded",
        slave_id, vfd_model,
        values.get("frequency", 0.0),
        values.get("current", 0.0),
        len(values),
    )

    # Some models keep a value outside the main block (e.g. Yaskawa dc_voltage
    # at 0x0046 — the drive rejects a read long enough to reach it). Each
    # extra read is its own Modbus transaction. A failure here is non-fatal:
    # log it and return the values we did get, rather than dropping the whole
    # device for the cycle.
    for extra in spec.get("extra_reads", []):
        try:
            er = client.read_holding_registers(
                address=extra["address"],
                count=extra["count"],
                device_id=slave_id,
            )
        except ModbusIOException as exc:
            log.warning("Slave %d [%s]: extra read at 0x%04X failed: %s",
                        slave_id, vfd_model, extra["address"], exc)
            continue
        if er.isError():
            log.warning("Slave %d [%s]: extra read at 0x%04X error: %s",
                        slave_id, vfd_model, extra["address"], er)
            continue
        for tag_name, reg_index, divisor in extra["registers"]:
            if tag_name is None:
                continue
            values[tag_name] = er.registers[reg_index] / divisor

    return values


# ---------------------------------------------------------------------------
# COMPONENT 5b — Main polling loop
# ---------------------------------------------------------------------------

def run(config: dict):
    """Poll all devices, buffer readings, and forward to the cloud.  Runs indefinitely.

    Each poll cycle:
      1. Iterate over every device in config["devices"] in order.
      2. Read Modbus registers for that device.
      3. For each decoded register value, look up the tag_definition_id in
         config["tag_definition_ids"].  If found, write one outbox row.
      4. After all devices are polled, forward the outbox to the cloud API.
      5. Sleep for polling_interval_seconds.

    Error handling:
      - ModbusIOException per device → skip that device, continue to next.
      - OSError errno 5 (USB device lost) → log CRITICAL, exit for systemd restart.
      - 5 consecutive unhandled exceptions → log CRITICAL, exit for systemd restart.
      - Any other exception → log ERROR with traceback, sleep 30 s, resume loop.

    Args:
        config: Validated dict from load_config().
    """
    poll_interval       = config["polling_interval_seconds"]
    modbus_port         = config["modbus_port"]
    modbus_baud         = config["modbus_baudrate"]
    company_id          = config["company_id"]
    api_base_url        = config["api_base_url"]
    devices             = config["devices"]
    tag_definition_ids  = config["tag_definition_ids"]
    # tag_definition_ids maps tag_name → tag_definition_id (int).
    # Tags not present in this dict are not stored (e.g. "ref_frequency").

    # One ModbusSerialClient for the entire RS485 bus.
    # All devices share the same port — they are distinguished by slave_id.
    # parity='N', stopbits=1, bytesize=8 are the standard RS485 settings used
    # by all three VFD models on this installation.
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
        "Gateway starting up — port=%s  baud=%d  poll=%ds  %d device(s)",
        modbus_port, modbus_baud, poll_interval, len(devices),
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

    # Consecutive-failure counter for the top-level exception handler.
    # Counts unhandled exceptions in a row; reset after any successful cycle.
    # At 5, we exit so systemd restarts the process in a clean state.
    _MAX_CONSECUTIVE_FAILURES = 5
    _consecutive_failures = 0

    while True:
        try:
            poll_cycle_start = time.time()
            timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            total_written  = 0   # outbox writes this cycle across all devices
            machines_failed = 0  # devices that returned None from read_modbus

            # ── Step 1: Poll each device ─────────────────────────────────
            for device in devices:
                dev_name    = device["name"]
                slave_id    = device["slave_id"]
                vfd_model   = device["vfd_model"]
                component_id = device["component_instance_id"]

                # Short label used in log messages: "Jet 33 / slave=1"
                dev_label = "{} / slave={}".format(dev_name, slave_id)

                # Read registers from this device.
                # Returns None on any Modbus error — skip this device for this cycle.
                values = read_modbus(client, slave_id, vfd_model)

                if values is None:
                    # Modbus failure is already logged inside read_modbus().
                    # We do NOT count this as a consecutive failure — it is a
                    # handled condition, not an unhandled exception.
                    machines_failed += 1
                    log.warning("[%s] Skipping — no data this cycle", dev_label)
                    continue

                # ── Step 2: Write each tag value to the outbox ───────────
                # For each decoded register, look up its tag_definition_id.
                # Tags absent from tag_definition_ids are silently skipped
                # (e.g. "ref_frequency" on INVT, "torque" on Yaskawa).
                written = 0
                for tag_name, value in values.items():
                    tag_def_id = tag_definition_ids.get(tag_name)
                    if tag_def_id is None:
                        # This tag exists in the register map but has no
                        # corresponding DB entry — skip silently.
                        log.debug(
                            "[%s] No tag_definition_id for '%s' — skipping",
                            dev_label, tag_name,
                        )
                        continue

                    # Note: the API uses value_num for numeric readings.
                    # The DataCreate schema has value_num (float) and
                    # value_text (str) — all VFD readings are numeric.
                    payload = {
                        "timestamp":             timestamp,
                        "component_instance_id": component_id,
                        "tag_definition_id":     tag_def_id,
                        "value_num":             value,
                        "company_id":            company_id,
                    }
                    write_to_outbox(payload)
                    written += 1

                total_written += written
                log.info(
                    "[%s] %d reading(s) written to outbox",
                    dev_label, written,
                )

            # ── Step 3: Forward outbox to cloud API ──────────────────────
            log.info(
                "Poll cycle complete — %d total reading(s) from %d device(s)",
                total_written, len(devices),
            )
            forward_outbox(token_manager, api_base_url)

            # ── Step 4: Post heartbeat ────────────────────────────────────
            # Call AFTER forward_outbox — outbox flushing is more important.
            # post_heartbeat() is fire-and-forget; failures are logged, not raised.
            poll_duration = time.time() - poll_cycle_start
            post_heartbeat(
                token_manager     = token_manager,
                api_base_url      = api_base_url,
                poll_duration_sec = poll_duration,
                machines_polled   = len(devices),
                machines_failed   = machines_failed,
            )

            # Reaching here means no unhandled exception occurred this cycle.
            _consecutive_failures = 0

        except Exception as exc:
            _consecutive_failures += 1

            # ── USB serial device lost (errno 5: Input/output error) ─────
            # The device node (/dev/ttyUSBx) was dropped by the kernel —
            # the file handle is dead.  Looping would spin forever on the
            # same broken handle.  Exit immediately so systemd restarts the
            # process (Restart=always, RestartSec=10) with a fresh handle.
            if isinstance(exc, OSError) and exc.errno == 5:
                log.critical(
                    "Serial device lost (errno 5: I/O error) — "
                    "exiting for systemd restart"
                )
                raise SystemExit(1)

            # ── Consecutive-failure threshold ─────────────────────────────
            # Five unhandled exceptions in a row without any successful cycle
            # means the process is stuck.  Exit so systemd can restart cleanly.
            if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                log.critical(
                    "%d consecutive unhandled exceptions — "
                    "exiting for systemd restart.  Last error:\n%s",
                    _consecutive_failures,
                    traceback.format_exc(),
                )
                raise SystemExit(1)

            # ── Recoverable unexpected error ──────────────────────────────
            # Log the full traceback for diagnosis, sleep 30 s, then resume.
            log.error(
                "Unexpected error in poll loop "
                "(failure %d/%d before forced restart):\n%s",
                _consecutive_failures,
                _MAX_CONSECUTIVE_FAILURES,
                traceback.format_exc(),
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
