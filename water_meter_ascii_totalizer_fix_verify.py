#!/usr/bin/env python3
"""
Eureka EU1000 water meter — bench verification of the torn-read fix.

Standalone script exercising the SAME logic just added to
gateway/gateway_service.py's EUREKA_EU1000 handling, so it can be watched
running live against the real meter over the USB dongle before it goes
anywhere near production again. Does NOT import gateway_service.py — the
combined-read + plausibility-check logic is reimplemented here inline,
deliberately kept in lockstep with gateway_service.py so this script is a
faithful stand-in, not an approximation.

Background (2026-08-06 incident): the previous bench scripts
(water_meter_ascii_totalizerFinal.py etc.) read the totalizer's integer part
(2007) and fraction part (2009) as two SEPARATE Modbus transactions. In
production, with the meter actively running (nonzero flow), this produced
torn reads — totalizer values swinging by 5,000-44,000 L between ~30s polls
while real flow (~3 m3/h) could only account for ~25-27 L. The magnitude
(all under 65536 = 2^16) matches a 16-bit high/low word tear.

Fix under test here:
  1. Registers 2007-2010 are read as ONE 4-register Modbus transaction
     (int part = regs[0:2], frac part = regs[2:4]) instead of two separate
     reads — closes the cross-transaction skew window entirely.
  2. A plausibility check on top: reject any reading that either (a)
     decreased from the last accepted value (a real totalizer is
     monotonic), or (b) implies a flow rate exceeding a generous ceiling
     (33.33 L/s — see _MAX_PLAUSIBLE_RATE_L_PER_SEC below for the
     derivation). The very first reading is always accepted unconditionally
     (nothing to compare against yet).

Confirmed settings:
  - Baud: 9600
  - Parity: Odd
  - Data bits: 7
  - Framing: ASCII (FramerType.ASCII) — the setting that made the meter
    respond at all; every pre-ASCII-framing test got total silence.
  - Slave ID: 2 — confirmed via production polling 2026-08-06. NOTE: an
    earlier session recorded slave=1 as "confirmed by reading the meter's
    own front-panel menu," but that appears to have been incorrect (or
    referred to a different setting) — every bench script in this repo
    (water_meter_scan.py, water_meter_totalizer_match.py,
    water_meter_combined_test.py, water_meter_ascii_totalizerFinal.py) and
    tonight's live production polling all agree on slave=2. Using 2 here.

Run and watch it for several minutes: flow_totalizer should only ever
increase (or hold steady if flow is briefly zero), by an amount consistent
with the printed flow_instantaneous reading times the elapsed time.
"""

import time
import struct
from datetime import datetime

from pymodbus.client import ModbusSerialClient
from pymodbus.framer import FramerType

# --- Serial config — update PORT if your dongle enumerates differently ---
PORT = "/dev/tty.usbserial-A50285BI"
SLAVE_ID = 2
POLL_INTERVAL_SEC = 30  # matches production polling cadence

# --- Plausibility-check constants — identical derivation to gateway_service.py ---
# Confirmed running flow ~3 m3/h. Generous realistic max flow ceiling = 4x
# observed = 12 m3/h (an assumption, not a manufacturer spec — no rated max
# flow for this install is documented anywhere). x10 safety margin:
# 120 m3/h = 33.33 L/s.
MAX_PLAUSIBLE_RATE_L_PER_SEC = 33.33


def decode_float32(regs, idx):
    """Big-endian IEEE-754 float from regs[idx], regs[idx+1] (high word first)."""
    packed = struct.pack(">HH", regs[idx], regs[idx + 1])
    return struct.unpack(">f", packed)[0]


def decode_uint32(regs, idx):
    """32-bit unsigned long from regs[idx], regs[idx+1] (high word first)."""
    return (regs[idx] << 16) | regs[idx + 1]


def read_eu1000(client):
    """Read instantaneous flow + combined totalizer block. Returns
    (flow_instantaneous, totalizer_total) or (None, None) on any Modbus error."""
    try:
        r_flow = client.read_holding_registers(address=2001, count=2, device_id=SLAVE_ID)
        if r_flow is None or r_flow.isError():
            print(f"  [read error] flow block: {r_flow}")
            return None, None
        flow_val = decode_float32(r_flow.registers, 0)

        # Combined 4-register read: 2007-2010 in ONE transaction.
        # regs[0:2] = totalizer integer part (uint32), regs[2:4] = fraction part (float32).
        r_tot = client.read_holding_registers(address=2007, count=4, device_id=SLAVE_ID)
        if r_tot is None or r_tot.isError():
            print(f"  [read error] totalizer block: {r_tot}")
            return flow_val, None
        int_part = decode_uint32(r_tot.registers, 0)
        frac_part = decode_float32(r_tot.registers, 2)
        total = int_part + frac_part

        return flow_val, total
    except Exception as exc:
        print(f"  [exception] {exc}")
        return None, None


def check_plausibility(candidate_total, last_state):
    """Mirrors gateway_service.py's EU1000 plausibility check exactly.

    last_state: (last_accepted_total, last_accepted_time) or None.
    Returns (accepted: bool, reason: str, new_last_state).
    """
    now = time.time()

    if last_state is None:
        return True, "first reading — nothing to compare against", (candidate_total, now)

    last_total, last_time = last_state
    elapsed = max(now - last_time, 0.01)
    delta = candidate_total - last_total
    max_plausible = MAX_PLAUSIBLE_RATE_L_PER_SEC * elapsed

    if delta < 0:
        reason = f"DECREASE of {-delta:.2f} L (totalizer must be monotonic)"
        return False, reason, last_state  # cache unchanged

    if delta > max_plausible:
        reason = (
            f"jump of {delta:.2f} L in {elapsed:.1f}s exceeds plausible max "
            f"{max_plausible:.2f} L ({MAX_PLAUSIBLE_RATE_L_PER_SEC} L/s ceiling)"
        )
        return False, reason, last_state  # cache unchanged

    reason = f"delta {delta:+.2f} L in {elapsed:.1f}s, within {max_plausible:.2f} L window"
    return True, reason, (candidate_total, now)


def main():
    client = ModbusSerialClient(
        port=PORT, baudrate=9600, parity="O", stopbits=1, bytesize=7,
        timeout=1, framer=FramerType.ASCII,
    )

    if not client.connect():
        print(f"Could not open {PORT}.")
        return

    print(f"Connected to {PORT} @ 9600-O-7, ASCII framing, slave={SLAVE_ID}.")
    print(f"Polling every {POLL_INTERVAL_SEC}s. Ctrl+C to stop.\n")
    print(f"{'Time':<10} {'Flow (m3/h)':>12}  {'Totalizer (L)':>16}  Status")
    print("-" * 70)

    last_state = None  # (last_accepted_total, last_accepted_time)
    accepted_count = 0
    rejected_count = 0

    try:
        while True:
            ts = datetime.now().strftime("%H:%M:%S")
            flow_val, candidate_total = read_eu1000(client)

            if flow_val is None:
                print(f"{ts:<10} {'—':>12}  {'—':>16}  MODBUS READ FAILED")
            elif candidate_total is None:
                print(f"{ts:<10} {flow_val:>12.4f}  {'—':>16}  TOTALIZER READ FAILED (flow OK)")
            else:
                accepted, reason, last_state = check_plausibility(candidate_total, last_state)
                if accepted:
                    accepted_count += 1
                    status = f"ACCEPTED — {reason}"
                else:
                    rejected_count += 1
                    status = f"REJECTED — {reason}"
                print(f"{ts:<10} {flow_val:>12.4f}  {candidate_total:>16.2f}  {status}")

            time.sleep(POLL_INTERVAL_SEC)

    except KeyboardInterrupt:
        print(f"\nStopped. {accepted_count} accepted, {rejected_count} rejected.")
        if last_state is not None:
            print(f"Last accepted totalizer: {last_state[0]:.2f} L")
    finally:
        client.close()


if __name__ == "__main__":
    main()
