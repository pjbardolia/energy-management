#!/usr/bin/env python3
"""
End-to-end API smoke test for the Energy Management platform.

This script uses ONLY the Python standard library (json + urllib + base64),
so it needs no pip installs and runs inside the API container exactly as-is.

It walks the create -> read chain in dependency order, captures the IDs each
step returns and feeds them into the next, and prints a clear PASS / FAIL line
for every endpoint, followed by a summary.

Phase 4d change: login now happens early (step 4) so that all subsequent
requests can send an Authorization: Bearer header.  company_id is decoded
from the JWT payload using stdlib base64 + json so no PyJWT install is needed.

HOW TO RUN (from a second terminal tab, in the project folder):
    docker compose exec api python test_api.py
"""

import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Guard A — must be set or the script exits immediately, before any network call.
# This script creates and modifies database rows.  Never run it against a
# database that contains real production data.
# ---------------------------------------------------------------------------
if not os.getenv("ALLOW_DESTRUCTIVE_TESTS"):
    print("ABORT: set ALLOW_DESTRUCTIVE_TESTS=1 to run this script.")
    print("This script creates and modifies database rows.")
    sys.exit(1)

# Guard C — configurable target URL.
# Inside the api container, uvicorn is at port 8000.
# From a Mac terminal against the live server:
#   TEST_API_URL=http://165.22.247.235:8001
BASE_URL = os.getenv("TEST_API_URL", "http://localhost:8000")

# Guard B credentials — both must be set or the script exits here, before any
# write.  Used in the pre-flight company check (runs after step 1, before step 2)
# to verify the target DB contains only test companies.
_preflight_user = os.getenv("TEST_PREFLIGHT_USER")
_preflight_pass = os.getenv("TEST_PREFLIGHT_PASSWORD")
if not _preflight_user or not _preflight_pass:
    print("ABORT: TEST_PREFLIGHT_USER and TEST_PREFLIGHT_PASSWORD must both be set.")
    print("Preflight verifies the target DB contains only test companies before any writes.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Tiny HTTP helper, built on urllib (standard library)
# ---------------------------------------------------------------------------
def request(method, path, body=None, token=None):
    """Send one HTTP request. Returns (status_code, raw_text, parsed_json_or_None)."""
    url = BASE_URL + path
    headers = {"Accept": "application/json"}
    data = None

    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        # Standard Bearer token header — picked up by OAuth2PasswordBearer
        # in FastAPI, which passes it to get_current_user().
        headers["Authorization"] = "Bearer " + token

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # 4xx / 5xx responses arrive here
        status = e.code
        raw = e.read().decode("utf-8")
    except urllib.error.URLError as e:
        # Could not reach the server at all
        return None, "CONNECTION ERROR: " + str(e.reason), None

    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None  # e.g. plain-text "Internal Server Error"

    return status, raw, parsed


def decode_jwt_payload(token: str) -> dict:
    """Decode the middle (payload) segment of a JWT without verifying the signature.

    JWTs are three base64url-encoded segments separated by dots:
        header.payload.signature

    The payload segment is base64url-encoded JSON.  We only need to read it
    here — signature verification is done by the server on every request.

    base64url uses '-' and '_' instead of '+' and '/'.  Python's urlsafe
    variant handles this.  JWT encoders strip padding ('=') so we re-pad
    to the nearest multiple of 4 before decoding.
    """
    segment = token.split(".")[1]                           # grab the middle segment
    padding = "=" * (4 - len(segment) % 4)                 # restore stripped '=' padding
    decoded = base64.urlsafe_b64decode(segment + padding)   # bytes
    return json.loads(decoded)                              # dict


# ---------------------------------------------------------------------------
# Result tracking + pretty printing
# ---------------------------------------------------------------------------
results = []  # list of (step, status, ok, expected_fail, detail)


def record(step, status, ok, detail="", expected_fail=False):
    results.append((step, status, ok, expected_fail, detail))
    if ok:
        mark = "PASS  "
    elif expected_fail:
        mark = "FAIL* "  # known / expected failure
    else:
        mark = "FAIL  "
    status_str = str(status) if status is not None else "---"
    print("[{}] {:<40} HTTP {:<4} {}".format(mark, step, status_str, detail))


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------
print("=" * 72)
print("Energy Management API  --  end-to-end smoke test")
print("Target:", BASE_URL)
print("=" * 72)

# A unique suffix so re-running the script doesn't collide on unique fields.
suffix = str(int(time.time()))

# ---------------------------------------------------------------------------
# PUBLIC ENDPOINTS (no token required)
# Steps 1–4 establish the company + user + JWT that all later steps need.
# ---------------------------------------------------------------------------

# 1) Health -----------------------------------------------------------------
status, raw, parsed = request("GET", "/health")
record("GET /health", status, status == 200, raw[:60])

# Guard B — pre-flight company check ----------------------------------------
# Log in with the pre-flight credentials and call GET /companies BEFORE any
# write.  Abort if the target DB contains any company whose name does not
# include "test" (case-insensitive).  An empty company list is allowed (fresh DB).
print()
print("-- Pre-flight company check --")
_pf_login_status, _, _pf_parsed = request(
    "POST", "/login",
    {"username": _preflight_user, "password": _preflight_pass},
)
if (_pf_login_status != 200
        or not isinstance(_pf_parsed, dict)
        or not _pf_parsed.get("access_token")):
    print("ABORT: Pre-flight login failed (HTTP {}).".format(_pf_login_status))
    print("Check TEST_PREFLIGHT_USER / TEST_PREFLIGHT_PASSWORD credentials.")
    sys.exit(1)

_pf_token = _pf_parsed["access_token"]
_, _, _pf_companies = request("GET", "/companies", token=_pf_token)
if not isinstance(_pf_companies, list):
    print("ABORT: Pre-flight GET /companies failed — cannot verify target database.")
    sys.exit(1)

for _c in _pf_companies:
    if "test" not in (_c.get("company_name") or "").lower():
        print('ABORT: Found non-test company "{}" (id={}).'.format(
            _c.get("company_name"), _c.get("id")))
        print("Refusing to run destructive tests against this database.")
        sys.exit(1)

print("Pre-flight passed: {} existing company/companies are all test fixtures.".format(
    len(_pf_companies)))
print()

# 2) Create company ---------------------------------------------------------
# POST /companies is public — company must exist before we can create a user.
status, raw, parsed = request(
    "POST", "/companies",
    {"company_name": "Test Factory " + suffix, "address": "123 Test Road"},
)
company_id = parsed.get("id") if isinstance(parsed, dict) else None
record("POST /companies", status, status == 201 and company_id is not None,
       "id=" + str(company_id) if company_id else raw[:90])

# 3) Create user ------------------------------------------------------------
# POST /users is public — user must exist before we can log in.
# We create the user here (before the catalog work) so login is available
# immediately for all protected endpoints below.
user_id = None
if company_id:
    status, raw, parsed = request(
        "POST", "/users",
        {"username": "tester_" + suffix, "password": "Test1234",
         "role": "admin", "company_id": company_id},
    )
    user_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /users", status, status == 201 and user_id is not None,
           "id=" + str(user_id) if user_id else raw[:90])
else:
    record("POST /users", None, False, "skipped: no company_id")

# 4) Login — get JWT --------------------------------------------------------
# POST /login is public.  After this step every protected endpoint must send
# Authorization: Bearer <token> or it receives HTTP 401.
#
# We also decode the JWT payload here to extract company_id so the catalog
# POST bodies match the authenticated tenant without needing a separate lookup.
token = None
jwt_company_id = None
if user_id:
    status, raw, parsed = request(
        "POST", "/login",
        {"username": "tester_" + suffix, "password": "Test1234"},
    )
    token_type = parsed.get("token_type") if isinstance(parsed, dict) else None
    token = parsed.get("access_token") if isinstance(parsed, dict) else None
    login_ok = bool(token) and token_type == "bearer"

    if login_ok:
        # Decode the JWT payload to read company_id without a DB round-trip.
        # The server already validated credentials — we just need the claim.
        try:
            jwt_payload = decode_jwt_payload(token)
            jwt_company_id = jwt_payload.get("company_id")
        except Exception as e:
            # If decoding fails the later steps will use company_id from step 2.
            print("  [WARN] Could not decode JWT payload: {}".format(e))
            jwt_company_id = company_id

    record("POST /login", status, login_ok,
           "token received, company_id={}".format(jwt_company_id) if login_ok
           else "token_type=" + str(token_type))
else:
    record("POST /login", None, False, "skipped: no user_id")

# Use company_id from JWT (authoritative) or fall back to step 2's value.
# They should always match — if they don't, something is wrong with login.
effective_company_id = jwt_company_id or company_id


# ===========================================================================
# PROTECTED ENDPOINTS (all require Authorization: Bearer <token>)
# ===========================================================================

# 5) Create department (needs company_id + token) ---------------------------
dept_id = None
if effective_company_id and token:
    status, raw, parsed = request(
        "POST", "/departments",
        {"name": "Dyeing", "description": "Dyeing department",
         "company_id": effective_company_id},
        token=token,
    )
    dept_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /departments", status, status == 201 and dept_id is not None,
           "id=" + str(dept_id) if dept_id else raw[:90])
else:
    record("POST /departments", None, False, "skipped: no company_id or token")


# ===========================================================================
# Phase 2 catalogue chain
# Dependency order: machine_type → component_types → tag_definitions → links
# ===========================================================================

# 6) Create machine type ----------------------------------------------------
machine_type_id = None
if effective_company_id and token:
    status, raw, parsed = request(
        "POST", "/machine-types",
        {"name": "Jet Dyeing Machine", "company_id": effective_company_id},
        token=token,
    )
    machine_type_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /machine-types", status, status == 201 and machine_type_id is not None,
           "id=" + str(machine_type_id) if machine_type_id else raw[:90])
else:
    record("POST /machine-types", None, False, "skipped: no company_id or token")

# 7-9) Create component types (×3) ------------------------------------------
reel_motor_id = circ_pump_id = vessel_id = None
COMPONENT_TYPES = [
    ("Reel Motor",       "reel_motor_id"),
    ("Circulation Pump", "circ_pump_id"),
    ("Vessel",           "vessel_id"),
]
if effective_company_id and token:
    for ct_name, var_name in COMPONENT_TYPES:
        status, raw, parsed = request(
            "POST", "/component-types",
            {"name": ct_name, "company_id": effective_company_id},
            token=token,
        )
        ct_id = parsed.get("id") if isinstance(parsed, dict) else None
        record(
            "POST /component-types ({})".format(ct_name), status,
            status == 201 and ct_id is not None,
            "id=" + str(ct_id) if ct_id else raw[:90],
        )
        if var_name == "reel_motor_id":
            reel_motor_id = ct_id
        elif var_name == "circ_pump_id":
            circ_pump_id = ct_id
        elif var_name == "vessel_id":
            vessel_id = ct_id
else:
    for ct_name, _ in COMPONENT_TYPES:
        record("POST /component-types ({})".format(ct_name), None, False,
               "skipped: no company_id or token")

# 10-19) Create tag definitions (×10) ---------------------------------------
# 9 float tags (numeric sensor readings) + 1 text tag (fault codes).
tag_ids = {}  # maps short name -> tag_definition id

TAG_SPECS = [
    # (short_name,     key,              display_name,       unit,  data_type)
    # 'key' is the stable slug posted to the API (gateway/frontend contract).
    # 'short_name' is the local tag_ids dict key used by LINK_SPECS below.
    # They differ for DC Bus Voltage: the legacy short_name "bus_voltage" keeps
    # LINK_SPECS working; the API key is the gateway contract slug "dc_voltage".
    ("rpm",            "rpm",            "Rotation Speed",   "rpm", "float"),
    ("torque",         "torque",         "Output Torque",    "Nm",  "float"),
    ("current",        "current",        "Output Current",   "A",   "float"),
    ("bus_voltage",    "dc_voltage",     "DC Bus Voltage",   "V",   "float"),
    ("output_voltage", "output_voltage", "Output Voltage",   "V",   "float"),
    ("frequency",      "frequency",      "Output Frequency", "Hz",  "float"),
    ("power",          "power",          "Output Power",     "kW",  "float"),
    ("temperature",    "temperature",    "Temperature",      "°C",  "float"),
    ("pressure",       "pressure",       "Pressure",         "bar", "float"),
    ("fault_code",     "fault_code",     "Fault Code",       "",    "text"),
]

if effective_company_id and token:
    for short, key, display, unit, dtype in TAG_SPECS:
        status, raw, parsed = request(
            "POST", "/tag-definitions",
            {"name": display, "key": key, "unit": unit, "data_type": dtype,
             "company_id": effective_company_id},
            token=token,
        )
        tid = parsed.get("id") if isinstance(parsed, dict) else None
        record(
            "POST /tag-definitions ({})".format(short), status,
            status == 201 and tid is not None,
            "id=" + str(tid) if tid else raw[:90],
        )
        if tid:
            tag_ids[short] = tid
else:
    for short, *_ in TAG_SPECS:
        record("POST /tag-definitions ({})".format(short), None, False,
               "skipped: no company_id or token")

# 20-22) Link tags to component types ---------------------------------------
# POST /component-types/{id}/tags accepts a batch body with all tag IDs at once.
# The endpoint is idempotent (skips duplicates) and always returns 201.

LINK_SPECS = [
    # (label,        component_type_id_var,  [tag short names])
    ("reel_motor", reel_motor_id, ["rpm", "torque", "current", "bus_voltage",
                                   "output_voltage", "frequency", "power"]),
    ("circ_pump",  circ_pump_id,  ["rpm", "current", "bus_voltage",
                                   "output_voltage", "frequency", "power"]),
    ("vessel",     vessel_id,     ["temperature", "pressure", "fault_code"]),
]

if effective_company_id and token:
    for label, ct_id, tag_names in LINK_SPECS:
        if ct_id is None:
            record("POST /component-types/{}/tags".format(label), None, False,
                   "skipped: component_type_id is None")
            continue

        # Resolve short names to IDs; bail if any tag creation failed earlier.
        resolved_ids = [tag_ids[n] for n in tag_names if n in tag_ids]
        if len(resolved_ids) != len(tag_names):
            record("POST /component-types/{}/tags".format(label), None, False,
                   "skipped: {} tag IDs missing".format(
                       len(tag_names) - len(resolved_ids)))
            continue

        status, raw, parsed = request(
            "POST", "/component-types/{}/tags".format(ct_id),
            {"tag_definition_ids": resolved_ids, "company_id": effective_company_id},
            token=token,
        )
        p = parsed if isinstance(parsed, dict) else {}
        n_created = len(p.get("created", []))
        n_skipped = len(p.get("skipped", []))
        record(
            "POST /component-types/{}/tags".format(label), status,
            status == 201 and n_created + n_skipped == len(tag_names),
            "created={} skipped={}".format(n_created, n_skipped),
        )
else:
    for label, _, tags in LINK_SPECS:
        record("POST /component-types/{}/tags".format(label), None, False,
               "skipped: no company_id or token")

# 23-26) GET catalogue read-backs -------------------------------------------
status, raw, parsed = request("GET", "/machine-types", token=token)
count = len(parsed) if isinstance(parsed, list) else "?"
record("GET /machine-types", status,
       status == 200 and isinstance(parsed, list) and len(parsed) >= 1,
       "rows=" + str(count))

status, raw, parsed = request("GET", "/component-types", token=token)
count = len(parsed) if isinstance(parsed, list) else "?"
record("GET /component-types", status,
       status == 200 and isinstance(parsed, list) and len(parsed) >= 3,
       "rows=" + str(count))

status, raw, parsed = request("GET", "/tag-definitions", token=token)
count = len(parsed) if isinstance(parsed, list) else "?"
record("GET /tag-definitions", status,
       status == 200 and isinstance(parsed, list) and len(parsed) >= 10,
       "rows=" + str(count))

# GET /component-types/{vessel_id}/tags — server-side filtered by component_type_id.
if vessel_id:
    status, raw, parsed = request(
        "GET", "/component-types/{}/tags".format(vessel_id), token=token)
    count = len(parsed) if isinstance(parsed, list) else 0
    record("GET /component-types/vessel/tags", status,
           status == 200 and count == 3,
           "rows=" + str(count))
else:
    record("GET /component-types/vessel/tags", None, False, "skipped: no vessel_id")


# ===========================================================================
# Physical layer — machines, components, and telemetry
# These now pass because Phase 4d wires up JWT auth, not expected_fail any more.
# ===========================================================================

# 27) Create machine --------------------------------------------------------
machine_id = None
if effective_company_id and machine_type_id and dept_id and token:
    status, raw, parsed = request(
        "POST", "/machines",
        {
            "name": "Jet 33",
            "machine_type_id": machine_type_id,
            "description": "Soft flow jet dyeing machine",
            "company_id": effective_company_id,
            "department_id": dept_id,
        },
        token=token,
    )
    machine_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /machines", status, status == 201 and machine_id is not None,
           ("id=" + str(machine_id)) if machine_id else raw[:90])
else:
    record("POST /machines", None, False,
           "skipped: missing company_id, machine_type_id, dept_id, or token")

# 28) Create machine component -----------------------------------------------
component_id = None
if effective_company_id and circ_pump_id and machine_id and token:
    status, raw, parsed = request(
        "POST", "/machine-components",
        {
            "name": "Main Pump",
            "component_type_id": circ_pump_id,
            "machine_id": machine_id,
            "company_id": effective_company_id,
        },
        token=token,
    )
    component_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /machine-components", status, status == 201 and component_id is not None,
           ("id=" + str(component_id)) if component_id else raw[:90])
else:
    record("POST /machine-components", None, False,
           "skipped: missing company_id, circ_pump_id, machine_id, or token")

# 29) Create telemetry record -----------------------------------------------
# Uses the normalized schema: one value under one tag_definition_id.
freq_tag_id = tag_ids.get("frequency")
if effective_company_id and component_id and freq_tag_id and token:
    status, raw, parsed = request(
        "POST", "/data",
        {
            "timestamp": "2026-06-28T12:00:00",
            "component_instance_id": component_id,
            "tag_definition_id": freq_tag_id,
            "value_num": 50.0,
            "company_id": effective_company_id,
        },
        token=token,
    )
    record("POST /data", status, status == 201, raw[:90])
else:
    record("POST /data", None, False,
           "skipped: missing company_id, component_id, freq_tag_id, or token")

# 30-34) Read-back GETs -----------------------------------------------------
# All GETs now require a token and return only the authenticated tenant's rows.
for path in ["/companies", "/departments", "/machines", "/machine-components", "/data"]:
    status, raw, parsed = request("GET", path, token=token)
    count = len(parsed) if isinstance(parsed, list) else "?"
    record("GET " + path, status, status == 200, "rows=" + str(count))

# 35) GET /machines/live — fleet live view ----------------------------------
status, raw, parsed = request("GET", "/machines/live", token=token)
live_ok = (
    status == 200
    and isinstance(parsed, list)
    and (not parsed or ("tags" in parsed[0] and "machine_id" in parsed[0]))
)
record("GET /machines/live", status, live_ok,
       "machines={}".format(len(parsed) if isinstance(parsed, list) else "?"))

# 36) GET /machines/{id}/live — single machine live view --------------------
# Uses machine_id from step 27.  404 acceptable if machine has no telemetry yet.
if machine_id:
    status, raw, parsed = request(
        "GET", "/machines/{}/live".format(machine_id), token=token)
    single_ok = status in (200, 404) and (
        status == 404 or "tags" in (parsed or {}))
    record("GET /machines/{}/live".format(machine_id), status, single_ok, raw[:90])
else:
    record("GET /machines/{id}/live", None, False, "skipped: no machine_id")

# 37) GET /machines/{id}/history — time-bucketed history --------------------
# 404 acceptable if the machine has no telemetry yet.
if machine_id:
    status, raw, parsed = request(
        "GET", "/machines/{}/history?hours=1".format(machine_id), token=token)
    hist_ok = status in (200, 404) and (
        status == 404 or (
            "data" in (parsed or {}) and "machine_id" in (parsed or {}) and
            (not parsed["data"] or "tags" in parsed["data"][0])
        ))
    record("GET /machines/{}/history".format(machine_id), status, hist_ok, raw[:90])
else:
    record("GET /machines/{id}/history", None, False, "skipped: no machine_id")

# 38) GET /fleet/summary — aggregated fleet KPIs ----------------------------
status, raw, parsed = request("GET", "/fleet/summary", token=token)
summary_ok = (
    status == 200
    and isinstance(parsed, dict)
    and "total_machines" in (parsed or {})
    and "running" in (parsed or {})
    and "total_power_kw" in (parsed or {})
)
record("GET /fleet/summary", status, summary_ok,
       "total={} running={}".format(
           (parsed or {}).get("total_machines", "?"),
           (parsed or {}).get("running", "?"),
       ))

# ===========================================================================
# Batch telemetry ingest (Phase 5d)
# ===========================================================================

# 39) POST /data/batch — 50 valid readings → 202, accepted == 50 ───────────
freq_tag_id = tag_ids.get("frequency")
if component_id and freq_tag_id and token:
    batch_readings = [
        {
            "timestamp":             "2026-06-28T{:02d}:{:02d}:00".format(i // 60, i % 60),
            "component_instance_id": component_id,
            "tag_definition_id":     freq_tag_id,
            "value_num":             49.0 + (i * 0.02),
        }
        for i in range(50)
    ]
    status, raw, parsed = request(
        "POST", "/data/batch", {"readings": batch_readings}, token=token
    )
    batch_ok = (
        status == 202
        and isinstance(parsed, dict)
        and parsed.get("accepted") == 50
    )
    record("POST /data/batch (50 valid rows)", status, batch_ok,
           "accepted={}".format((parsed or {}).get("accepted", "?")) if parsed else raw[:90])
else:
    record("POST /data/batch (50 valid rows)", None, False,
           "skipped: missing component_id, freq_tag_id, or token")

# 40) POST /data/batch cross-tenant → 403 ─────────────────────────────────
# Create a second test company + user so we have a token from a different tenant.
# Then use company-1's component_id with that foreign token — must return 403.
company2_id = None
token2 = None
if component_id and freq_tag_id:
    _s, _, _p = request(
        "POST", "/companies",
        {"company_name": "Test Factory B " + suffix, "address": "456 Test Ave"},
    )
    company2_id = _p.get("id") if isinstance(_p, dict) and _s == 201 else None

    if company2_id:
        _s, _, _p = request(
            "POST", "/users",
            {"username": "tester_b_" + suffix, "password": "Test1234",
             "role": "admin", "company_id": company2_id},
        )
        if _s == 201:
            _s, _, _p = request(
                "POST", "/login",
                {"username": "tester_b_" + suffix, "password": "Test1234"},
            )
            token2 = _p.get("access_token") if isinstance(_p, dict) and _s == 200 else None

    if token2:
        status, raw, parsed = request(
            "POST", "/data/batch",
            {"readings": [{
                "timestamp":             "2026-06-28T15:00:00",
                "component_instance_id": component_id,   # owned by company 1
                "tag_definition_id":     freq_tag_id,
                "value_num":             30.0,
            }]},
            token=token2,   # authenticated as company 2
        )
        record("POST /data/batch (cross-tenant → 403)", status, status == 403, raw[:90])
    else:
        record("POST /data/batch (cross-tenant → 403)", None, False,
               "skipped: could not set up second test company/user")
else:
    record("POST /data/batch (cross-tenant → 403)", None, False,
           "skipped: no component_id or freq_tag_id")

# 41) POST /data/batch empty array → 422 (Pydantic min_length=1) ──────────
status, raw, parsed = request(
    "POST", "/data/batch", {"readings": []}, token=token
)
record("POST /data/batch (empty → 422)", status, status == 422, raw[:60])

# 42) POST /data backward compat — existing single-row endpoint unchanged ──
if effective_company_id and component_id and freq_tag_id and token:
    status, raw, parsed = request(
        "POST", "/data",
        {
            "timestamp":             "2026-06-28T16:00:00",
            "component_instance_id": component_id,
            "tag_definition_id":     freq_tag_id,
            "value_num":             50.0,
            "company_id":            effective_company_id,
        },
        token=token,
    )
    record("POST /data (backward compat)", status, status == 201, raw[:90])
else:
    record("POST /data (backward compat)", None, False,
           "skipped: missing required IDs")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("=" * 72)
passed = sum(1 for _, _, ok, _, _ in results if ok)
known  = sum(1 for _, _, ok, ef, _ in results if (not ok) and ef)
broken = sum(1 for _, _, ok, ef, _ in results if (not ok) and not ef)
print("SUMMARY: {} passed,  {} known/expected failures,  {} unexpected failures"
      .format(passed, known, broken))

if known:
    print("\nKnown failures:")
    for step, status, ok, ef, detail in results:
        if (not ok) and ef:
            print("   * {:<40} HTTP {}".format(step, status))

if broken:
    print("\nUNEXPECTED failures (these need a closer look):")
    for step, status, ok, ef, detail in results:
        if (not ok) and not ef:
            print("   ! {:<40} HTTP {}   {}".format(step, status, detail))

print("=" * 72)
