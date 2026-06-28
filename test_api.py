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
        mark = "FAIL* "  # known / expected failure (refactor not finished)
    else:
        mark = "FAIL  "
    status_str = str(status) if status is not None else "---"
    print("[{}] {:<26} HTTP {:<4} {}".format(mark, step, status_str, detail))


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
record("POST /companies", status, status == 200 and company_id is not None,
       "id=" + str(company_id) if company_id else raw[:90])

# 3) Create department (needs company_id) -----------------------------------
dept_id = None
if company_id:
    status, raw, parsed = request(
        "POST", "/departments",
        {"name": "Dyeing", "description": "Dyeing department", "company_id": company_id},
    )
    dept_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /departments", status, status == 200 and dept_id is not None,
           "id=" + str(dept_id) if dept_id else raw[:90])
else:
    record("POST /departments", None, False, "skipped: no company_id")

# 4) Create machine (KNOWN MISMATCH: schema/router vs model) ----------------
machine_id = None
status, raw, parsed = request(
    "POST", "/machines",
    {"name": "Jet 33", "machine_type": "Jet Dyeing Machine", "description": "Soft flow",
     "company_id": company_id or 1, "department_id": dept_id or 1},
)
machine_id = parsed.get("id") if isinstance(parsed, dict) else None
record("POST /machines", status, status == 200 and machine_id is not None,
       ("id=" + str(machine_id)) if machine_id else raw[:90], expected_fail=True)

# 5) Create machine component (KNOWN MISMATCH) ------------------------------
component_id = None
status, raw, parsed = request(
    "POST", "/machine-components",
    {"name": "Main Pump", "component_type": "Pump", "machine_id": machine_id or 1},
)
component_id = parsed.get("id") if isinstance(parsed, dict) else None
record("POST /machine-components", status, status == 200 and component_id is not None,
       ("id=" + str(component_id)) if component_id else raw[:90], expected_fail=True)

# 6) Create user (needs company_id) -----------------------------------------
if company_id:
    status, raw, parsed = request(
        "POST", "/users",
        {"username": "tester_" + suffix, "password": "Test1234",
         "role": "admin", "company_id": company_id},
    )
    user_id = parsed.get("id") if isinstance(parsed, dict) else None
    record("POST /users", status, status == 200 and user_id is not None,
           "id=" + str(user_id) if user_id else raw[:90])
else:
    record("POST /users", None, False, "skipped: no company_id")

# 7) Login ------------------------------------------------------------------
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

# 8) Create data (KNOWN MISMATCH: old wide-column schema vs new model) -------
status, raw, parsed = request(
    "POST", "/data",
    {"timestamp": "2026-06-28T12:00:00",
     "machine_id": machine_id or 1, "component_id": component_id or 1,
     "output_frequency": 50.0, "reference_frequency": 50.0, "dc_bus_voltage": 540.0,
     "output_voltage": 400.0, "output_current": 12.5, "rotation_speed": 1450.0,
     "output_power": 7.5, "output_torque": 48.0, "temperature": 65.0, "pressure": 2.5},
)
record("POST /data", status, status == 200, raw[:90], expected_fail=True)

# 9) Read-back GETs (all should return 200) ---------------------------------
for path in ["/companies", "/departments", "/machines", "/machine-components", "/data"]:
    status, raw, parsed = request("GET", path)
    count = len(parsed) if isinstance(parsed, list) else "?"
    record("GET " + path, status, status == 200, "rows=" + str(count))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("=" * 72)
passed = sum(1 for _, _, ok, _, _ in results if ok)
known = sum(1 for _, _, ok, ef, _ in results if (not ok) and ef)
broken = sum(1 for _, _, ok, ef, _ in results if (not ok) and not ef)
print("SUMMARY: {} passed,  {} known/expected failures,  {} unexpected failures"
      .format(passed, known, broken))

if known:
    print("\nKnown failures (the unfinished half of the refactor -- next on our list):")
    for step, status, ok, ef, detail in results:
        if (not ok) and ef:
            print("   * {:<26} HTTP {}".format(step, status))

if broken:
    print("\nUNEXPECTED failures (these need a closer look):")
    for step, status, ok, ef, detail in results:
        if (not ok) and not ef:
            print("   ! {:<26} HTTP {}   {}".format(step, status, detail))

print("=" * 72)
