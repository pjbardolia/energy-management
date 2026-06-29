#!/usr/bin/env python3
"""
End-to-end API smoke test for the Energy Management platform.

This script uses ONLY the Python standard library (json + urllib), so it needs
no pip installs and runs inside the API container exactly as-is.

It walks the create -> read chain in dependency order, captures the IDs each
step returns and feeds them into the next, and prints a clear PASS / FAIL line
for every endpoint, followed by a summary.

HOW TO RUN (from a second terminal tab, in the project folder):
    docker compose exec api python test_api.py
"""

import json
import time
import urllib.request
import urllib.error

# Inside the api container, uvicorn is listening on port 8000.
# (From your Mac's browser it's 8001, but this script runs *inside* the container.)
BASE_URL = "http://localhost:8000"


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

# 1) Health -----------------------------------------------------------------
status, raw, parsed = request("GET", "/health")
record("GET /health", status, status == 200, raw[:60])

# 2) Create company ---------------------------------------------------------
status, raw, parsed = request(
    "POST", "/companies",
    {"company_name": "Test Factory " + suffix, "address": "123 Test Road"},
)
company_id = parsed.get("id") if isinstance(parsed, dict) else None
record("POST /companies", status, status == 201 and company_id is not None,
       "id=" + str(company_id) if company_id else raw[:90])

# 3) Create department (needs company_id) -----------------------------------
dept_id = None
if company_id:
    status, raw, parsed = request(
        "POST", "/departments",
        {"name": "Dyeing", "description": "Dyeing department", "company_id": company_id},
    )
    dept_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /departments", status, status == 201 and dept_id is not None,
           "id=" + str(dept_id) if dept_id else raw[:90])
else:
    record("POST /departments", None, False, "skipped: no company_id")


# ===========================================================================
# Phase 2 catalogue chain
# Dependency order: machine_type → component_types → tag_definitions → links
# ===========================================================================

# 4) Create machine type ----------------------------------------------------
machine_type_id = None
if company_id:
    status, raw, parsed = request(
        "POST", "/machine-types",
        {"name": "Jet Dyeing Machine", "company_id": company_id},
    )
    machine_type_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /machine-types", status, status == 201 and machine_type_id is not None,
           "id=" + str(machine_type_id) if machine_type_id else raw[:90])
else:
    record("POST /machine-types", None, False, "skipped: no company_id")

# 5) Create component types (×3) --------------------------------------------
reel_motor_id = circ_pump_id = vessel_id = None
COMPONENT_TYPES = [
    ("Reel Motor",       "reel_motor_id"),
    ("Circulation Pump", "circ_pump_id"),
    ("Vessel",           "vessel_id"),
]
if company_id:
    for ct_name, var_name in COMPONENT_TYPES:
        status, raw, parsed = request(
            "POST", "/component-types",
            {"name": ct_name, "company_id": company_id},
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
        record("POST /component-types ({})".format(ct_name), None, False, "skipped: no company_id")

# 6) Create tag definitions (×10) ------------------------------------------
# 9 float tags (numeric sensor readings) + 1 text tag (fault codes).
tag_ids = {}  # maps short name -> tag_definition id

TAG_SPECS = [
    # (short_name,     display_name,       unit,  data_type)
    ("rpm",            "Rotation Speed",   "rpm", "float"),
    ("torque",         "Output Torque",    "Nm",  "float"),
    ("current",        "Output Current",   "A",   "float"),
    ("bus_voltage",    "DC Bus Voltage",   "V",   "float"),
    ("output_voltage", "Output Voltage",   "V",   "float"),
    ("frequency",      "Output Frequency", "Hz",  "float"),
    ("power",          "Output Power",     "kW",  "float"),
    ("temperature",    "Temperature",      "°C",  "float"),
    ("pressure",       "Pressure",         "bar", "float"),
    ("fault_code",     "Fault Code",       "",    "text"),
]

if company_id:
    for short, display, unit, dtype in TAG_SPECS:
        status, raw, parsed = request(
            "POST", "/tag-definitions",
            {"name": display, "unit": unit, "data_type": dtype, "company_id": company_id},
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
        record("POST /tag-definitions ({})".format(short), None, False, "skipped: no company_id")

# 7) Link tags to component types -------------------------------------------
# POST /component-types/{id}/tags accepts a batch body with all tag IDs at once.
# The endpoint is idempotent (skips duplicates) and always returns 201.

LINK_SPECS = [
    # (label,        component_type_id_var,  [tag short names])
    ("reel_motor", reel_motor_id, ["rpm", "torque", "current", "bus_voltage", "output_voltage", "frequency", "power"]),
    ("circ_pump",  circ_pump_id,  ["rpm", "current", "bus_voltage", "output_voltage", "frequency", "power"]),
    ("vessel",     vessel_id,     ["temperature", "pressure", "fault_code"]),
]

if company_id:
    for label, ct_id, tag_names in LINK_SPECS:
        if ct_id is None:
            record("POST /component-types/{}/tags".format(label), None, False,
                   "skipped: component_type_id is None")
            continue

        # Resolve short names to IDs; bail if any tag creation failed earlier.
        resolved_ids = [tag_ids[n] for n in tag_names if n in tag_ids]
        if len(resolved_ids) != len(tag_names):
            record("POST /component-types/{}/tags".format(label), None, False,
                   "skipped: {} tag IDs missing".format(len(tag_names) - len(resolved_ids)))
            continue

        status, raw, parsed = request(
            "POST", "/component-types/{}/tags".format(ct_id),
            {"tag_definition_ids": resolved_ids, "company_id": company_id},
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
        record("POST /component-types/{}/tags".format(label), None, False, "skipped: no company_id")

# 8) GET catalogue read-backs -----------------------------------------------
status, raw, parsed = request("GET", "/machine-types")
count = len(parsed) if isinstance(parsed, list) else "?"
record("GET /machine-types", status,
       status == 200 and isinstance(parsed, list) and len(parsed) >= 1,
       "rows=" + str(count))

status, raw, parsed = request("GET", "/component-types")
count = len(parsed) if isinstance(parsed, list) else "?"
record("GET /component-types", status,
       status == 200 and isinstance(parsed, list) and len(parsed) >= 3,
       "rows=" + str(count))

status, raw, parsed = request("GET", "/tag-definitions")
count = len(parsed) if isinstance(parsed, list) else "?"
record("GET /tag-definitions", status,
       status == 200 and isinstance(parsed, list) and len(parsed) >= 10,
       "rows=" + str(count))

# GET /component-types/{vessel_id}/tags — server-side filtered, no client filtering needed.
if vessel_id:
    status, raw, parsed = request("GET", "/component-types/{}/tags".format(vessel_id))
    count = len(parsed) if isinstance(parsed, list) else 0
    record("GET /component-types/vessel/tags", status,
           status == 200 and count == 3,
           "rows=" + str(count))
else:
    record("GET /component-types/vessel/tags", None, False, "skipped: no vessel_id")


# ===========================================================================
# Phase 3 placeholder steps (expected_fail=True)
# Bodies now send the correct normalized fields — these will start passing
# once Phase 3 wires up auth enforcement and company scoping.
# ===========================================================================

# 9) Create machine ---------------------------------------------------------
machine_id = None
status, raw, parsed = request(
    "POST", "/machines",
    {
        "name": "Jet 33",
        "machine_type_id": machine_type_id or 1,
        "description": "Soft flow",
        "company_id": company_id or 1,
        "department_id": dept_id or 1,
    },
)
machine_id = parsed.get("id") if isinstance(parsed, dict) else None
record("POST /machines", status, status == 201 and machine_id is not None,
       ("id=" + str(machine_id)) if machine_id else raw[:90], expected_fail=True)

# 10) Create machine component -----------------------------------------------
component_id = None
status, raw, parsed = request(
    "POST", "/machine-components",
    {
        "name": "Main Pump",
        "component_type_id": circ_pump_id or 1,
        "machine_id": machine_id or 1,
        "company_id": company_id or 1,
    },
)
component_id = parsed.get("id") if isinstance(parsed, dict) else None
record("POST /machine-components", status, status == 201 and component_id is not None,
       ("id=" + str(component_id)) if component_id else raw[:90], expected_fail=True)

# 11) Create user (needs company_id) ----------------------------------------
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

# 12) Login -----------------------------------------------------------------
token = None
status, raw, parsed = request(
    "POST", "/login",
    {"username": "tester_" + suffix, "password": "Test1234"},
)
token_type = parsed.get("token_type") if isinstance(parsed, dict) else None
token = parsed.get("access_token") if isinstance(parsed, dict) else None
login_ok = bool(token) and token_type == "bearer"
record("POST /login", status, login_ok,
       "token received" if login_ok else "token_type=" + str(token_type))

# 13) Create telemetry record -----------------------------------------------
# Uses the normalized schema: one value under one tag_definition_id.
# component_id may be None if step 10 is still failing (Phase 3).
freq_tag_id = tag_ids.get("frequency")
status, raw, parsed = request(
    "POST", "/data",
    {
        "timestamp": "2026-06-28T12:00:00",
        "component_instance_id": component_id or 1,
        "tag_definition_id": freq_tag_id or 1,
        "value_num": 50.0,
        "company_id": company_id or 1,
    },
)
record("POST /data", status, status == 201, raw[:90], expected_fail=True)

# 14) Read-back GETs --------------------------------------------------------
for path in ["/companies", "/departments", "/machines", "/machine-components", "/data"]:
    status, raw, parsed = request("GET", path)
    count = len(parsed) if isinstance(parsed, list) else "?"
    record("GET " + path, status, status == 200, "rows=" + str(count))

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
    print("\nKnown failures (Phase 3 — auth + scoping not yet enforced):")
    for step, status, ok, ef, detail in results:
        if (not ok) and ef:
            print("   * {:<40} HTTP {}".format(step, status))

if broken:
    print("\nUNEXPECTED failures (these need a closer look):")
    for step, status, ok, ef, detail in results:
        if (not ok) and not ef:
            print("   ! {:<40} HTTP {}   {}".format(step, status, detail))

print("=" * 72)
