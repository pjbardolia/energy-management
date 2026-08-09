#!/usr/bin/env python3
"""
Bench-test script — Jet 11 Eureka EU1000 flowmeter, double-read confirmation.

Purpose: diagnose the totalizer tear observed under active flow (12/12
rejections in the previous bench test, see session notes 2026-08-06).
Confirmed via prior testing: idle meter reads are always self-consistent;
active-flow reads are essentially never self-consistent across the
int_part (registers 2007-2008) and frac_part (registers 2009-2010) when
read as a single 4-register 2007-2010 transaction. This suggests the two
parts are refreshed from separate internal update cycles inside the meter
itself, not (only) a cross-transaction Modbus timing gap.

This script does NOT touch gateway_service.py or production. It is a
standalone diagnostic tool.

Technique — "double-read confirmation":
  1. Read the 4-register totalizer block (2007-2010) once.
  2. Wait ~150ms.
  3. Read the same block again.
  4. Compare the two decoded totals directly against EACH OTHER (not
     against history). At ~3 m3/h, two reads 150ms apart should differ
     by well under 1 L if the meter's internal state is genuinely
     self-consistent at read time.
  5. If they agree (within tolerance): trust the pair, run it through
     the existing rate/monotonic check against history, log ACCEPTED.
  6. If they disagree: log the RAW register words from both reads
     (this is the key diagnostic output), retry up to MAX_RETRIES times
     before giving up on this poll cycle.

Confirmed settings (per production config.json / prior bench testing):
  - Port: update PORT below to match your dongle's device path
    (check with: ls /dev/tty.usbserial*)
  - 9600 baud, Odd parity, 7 data bits, 1 stop bit, ASCII framing
  - Slave ID = 2 (confirmed empirically via production polling AND
    matches all 4 prior historical bench scripts in the repo — the
    earlier "slave=1, confirmed via front-panel menu" note from
    2026-08-05 night appears to have been simply wrong)
  - Registers: 2001 (float32) = instantaneous flow, m3/h
               2007 (2 regs, uint32 hi/lo) = totalizer integer part, L
               2009 (2 regs, float32) = totalizer fraction part, L
"""

import struct
import time
from datetime import datetime

from pymodbus.client import ModbusSerialClient
from pymodbus.framer import FramerType

# ---------------------------------------------------------------------
# Config — update PORT if your dongle enumerates differently
# ---------------------------------------------------------------------
PORT = "/dev/tty.usbserial-A50285BI"
BAUDRATE = 9600
PARITY = "O"
BYTESIZE = 7
STOPBITS = 1
SLAVE_ID = 2

FLOW_REGISTER = 2001       # float32, 2 registers
TOTALIZER_REGISTER = 2007  # uint32 (hi/lo) + float32 (hi/lo), 4 registers total: 2007-2010

POLL_INTERVAL_SECONDS = 30
DOUBLE_READ_GAP_SECONDS = 0.15   # ~150ms between the two confirmation reads
MAX_RETRIES = 5
CONFIRMATION_TOLERANCE_LITERS = 1.0  # two reads 150ms apart should agree within this

# Plausibility check against history (same constants as production fix)
MAX_PLAUSIBLE_RATE_L_PER_SEC = 33.33  # derivation: 4x observed running rate (12 m3/h)
                                        # x 10x safety multiplier = 120 m3/h = 33.33 L/s
                                        # NOTE: 12 m3/h ceiling is a labeled ASSUMPTION,
                                        # not a manufacturer spec — correct if you know
                                        # the real max flow rating for this install.


def decode_float32(regs):
    """Big-endian word order float32 from two registers."""
    return struct.unpack('>f', struct.pack('>HH', regs[0], regs[1]))[0]


def decode_uint32(regs):
    """Big-endian word order uint32 from two registers."""
    return (regs[0] << 16) | regs[1]


def _read_holding_registers_compat(client, address, count):
    """
    pymodbus renamed the device-id keyword argument between versions:
    older 3.x used `slave=`, newer 3.7+ uses `device_id=`. Try both so
    this script works regardless of which version is installed.
    """
    try:
        return client.read_holding_registers(address=address, count=count, slave=SLAVE_ID)
    except TypeError:
        return client.read_holding_registers(address=address, count=count, device_id=SLAVE_ID)


def read_flow(client):
    rr = _read_holding_registers_compat(client, FLOW_REGISTER, 2)
    if rr.isError():
        raise IOError(f"Flow read error: {rr}")
    return decode_float32(rr.registers)


def read_totalizer_raw(client):
    """
    Single 4-register read spanning 2007-2010.
    Returns (raw_registers, int_part, frac_part, total).
    """
    rr = _read_holding_registers_compat(client, TOTALIZER_REGISTER, 4)
    if rr.isError():
        raise IOError(f"Totalizer read error: {rr}")
    regs = rr.registers  # [int_hi, int_lo, frac_hi, frac_lo]
    int_part = decode_uint32(regs[0:2])
    frac_part = decode_float32(regs[2:4])
    total = int_part + frac_part
    return regs, int_part, frac_part, total


def double_read_confirm(client):
    """
    Attempt up to MAX_RETRIES times to get two totalizer reads (~150ms apart)
    that agree with each other. Returns (total, raw1, raw2, attempts) on
    success, or (None, raw1, raw2, attempts) if all retries exhausted —
    with raw1/raw2 from the LAST attempt for diagnostic logging.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        regs1, int1, frac1, total1 = read_totalizer_raw(client)
        time.sleep(DOUBLE_READ_GAP_SECONDS)
        regs2, int2, frac2, total2 = read_totalizer_raw(client)

        diff = abs(total2 - total1)
        if diff <= CONFIRMATION_TOLERANCE_LITERS:
            return total1, regs1, regs2, attempt, diff, True
        # disagreement — log raw words for diagnosis, then retry
        print(
            f"    [attempt {attempt}/{MAX_RETRIES}] MISMATCH: "
            f"read1={regs1} (int={int1}, frac={frac1:.4f}, total={total1:.2f}) | "
            f"read2={regs2} (int={int2}, frac={frac2:.4f}, total={total2:.2f}) | "
            f"diff={diff:.2f} L"
        )

    # all retries exhausted — return last attempt's data for logging
    return None, regs1, regs2, MAX_RETRIES, diff, False


def main():
    client = ModbusSerialClient(
        port=PORT,
        baudrate=BAUDRATE,
        parity=PARITY,
        bytesize=BYTESIZE,
        stopbits=STOPBITS,
        framer=FramerType.ASCII,
        timeout=2,
    )

    if not client.connect():
        print(f"FAILED to connect to {PORT}")
        return

    print(f"Connected to {PORT} @ {BAUDRATE}-{PARITY}-{BYTESIZE}, ASCII framing, slave={SLAVE_ID}.")
    print(f"Polling every {POLL_INTERVAL_SECONDS}s, double-read gap {DOUBLE_READ_GAP_SECONDS*1000:.0f}ms, max {MAX_RETRIES} retries per cycle.")
    print("Ctrl+C to stop.\n")
    print(f"{'Time':<10} {'Flow (m3/h)':<12} {'Totalizer (L)':<16} {'Attempts':<10} {'Status'}")
    print("-" * 100)

    last_accepted_total = None
    last_accepted_time = None
    accepted_count = 0
    rejected_count = 0
    confirmation_failed_count = 0

    try:
        while True:
            cycle_start = time.time()
            now_str = datetime.now().strftime("%H:%M:%S")

            try:
                flow = read_flow(client)
            except IOError as e:
                print(f"{now_str:<10} FLOW READ ERROR: {e}")
                flow = None

            total, raw1, raw2, attempts, last_diff, confirmed = double_read_confirm(client)

            flow_str = f"{flow:.4f}" if flow is not None else "ERROR"

            if not confirmed:
                confirmation_failed_count += 1
                print(
                    f"{now_str:<10} {flow_str:<12} {'---':<16} {attempts:<10} "
                    f"REJECTED — could not get two agreeing reads after {attempts} attempts "
                    f"(last diff {last_diff:.2f} L; raw1={raw1}, raw2={raw2})"
                )
            else:
                # confirmed self-consistent pair — now check against history
                if last_accepted_total is None:
                    last_accepted_total = total
                    last_accepted_time = cycle_start
                    accepted_count += 1
                    print(
                        f"{now_str:<10} {flow_str:<12} {total:<16.2f} {attempts:<10} "
                        f"ACCEPTED — first confirmed reading"
                    )
                else:
                    elapsed = cycle_start - last_accepted_time
                    delta = total - last_accepted_total
                    max_plausible = MAX_PLAUSIBLE_RATE_L_PER_SEC * elapsed

                    if delta < 0:
                        rejected_count += 1
                        print(
                            f"{now_str:<10} {flow_str:<12} {total:<16.2f} {attempts:<10} "
                            f"REJECTED — confirmed pair, but DECREASE of {-delta:.2f} L vs history "
                            f"(totalizer must be monotonic)"
                        )
                    elif delta > max_plausible:
                        rejected_count += 1
                        print(
                            f"{now_str:<10} {flow_str:<12} {total:<16.2f} {attempts:<10} "
                            f"REJECTED — confirmed pair, but jump of {delta:.2f} L in {elapsed:.1f}s "
                            f"exceeds plausible max {max_plausible:.2f} L"
                        )
                    else:
                        accepted_count += 1
                        last_accepted_total = total
                        last_accepted_time = cycle_start
                        print(
                            f"{now_str:<10} {flow_str:<12} {total:<16.2f} {attempts:<10} "
                            f"ACCEPTED — delta +{delta:.2f} L in {elapsed:.1f}s, within {max_plausible:.2f} L window"
                        )

            elapsed_cycle = time.time() - cycle_start
            sleep_time = max(0, POLL_INTERVAL_SECONDS - elapsed_cycle)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n\nStopped.")
        print(f"Accepted: {accepted_count}")
        print(f"Rejected (confirmed but implausible vs history): {rejected_count}")
        print(f"Confirmation failed (double-read never agreed): {confirmation_failed_count}")
        if last_accepted_total is not None:
            print(f"Last accepted totalizer value: {last_accepted_total:.2f} L")
    finally:
        client.close()


if __name__ == "__main__":
    main()
