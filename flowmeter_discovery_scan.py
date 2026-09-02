#!/usr/bin/env python3
"""
Two-phase discovery script — NEW (unknown brand/model) flowmeter, Jet 11/12.

We do NOT know this meter's protocol (RTU vs ASCII), baud/parity/slave ID,
or register map. Do NOT assume it matches the old Eureka EU1000's settings
(9600, ASCII, 7-O-1, slave=2) — this is a different physical unit and
possibly a different manufacturer entirely.

PHASE 1 — DISCOVERY:
  Try combinations of framing (RTU / ASCII) x serial settings x slave IDs
  1-10, attempting a minimal read (holding + input registers, address 0).
  A Modbus EXCEPTION response still counts as "found" — it means we have
  the right framing/baud/parity/slave, just the wrong register address.
  Only a timeout/no-response means that combo is wrong.

PHASE 2 — REGISTER SCAN:
  Once Phase 1 finds a live combination, scan a wide register range with
  both function codes (03 holding, 04 input), printing raw register
  values AND common decoded interpretations (uint16, int16, uint32 hi/lo
  and lo/hi, float32 hi/lo and lo/hi) so you can visually match against
  whatever the meter's own front-panel display is currently showing for
  instantaneous flow and totalizer.

This does NOT touch gateway_service.py or production. Standalone bench
tool only.

BEFORE RUNNING:
  - Update PORT below (check: ls /dev/tty.usbserial*)
  - Have the meter's own front-panel display visible so you can compare
    live instantaneous flow / totalizer values against what this script
    finds — that comparison is how we confirm which register is which,
    same method that cracked the old Eureka EU1000.
"""

import struct
import time
from datetime import datetime

from pymodbus.client import ModbusSerialClient
from pymodbus.framer import FramerType

# ---------------------------------------------------------------------
PORT = "/dev/tty.usbserial-A50285BI"  # UPDATE if your dongle differs

BAUD_RATES = [9600]  # add 19200, 4800 here if 9600 finds nothing

# (bytesize, parity, stopbits) combos to try per framing.
RTU_SERIAL_CONFIGS = [
    (8, "N", 1),
    (8, "E", 1),
]
ASCII_SERIAL_CONFIGS = [
    (7, "O", 1),
    (7, "E", 1),
    (8, "N", 1),
]

SLAVE_ID_RANGE = range(1, 11)  # 1 through 10

DISCOVERY_TIMEOUT = 0.4  # short timeout per attempt to keep discovery fast
SCAN_TIMEOUT = 1.0

SCAN_ADDRESS_START = 0
SCAN_ADDRESS_END = 200  # adjust wider if nothing interesting found
# ---------------------------------------------------------------------


def _read_regs_compat(client, address, count, slave, function="holding"):
    """Handle pymodbus slave= vs device_id= kwarg rename across versions."""
    fn = client.read_holding_registers if function == "holding" else client.read_input_registers
    try:
        return fn(address=address, count=count, slave=slave)
    except TypeError:
        return fn(address=address, count=count, device_id=slave)


def phase1_discover():
    print("=" * 70)
    print("PHASE 1 — DISCOVERY: finding framing / serial settings / slave ID")
    print("=" * 70)

    found = []

    combos = []
    for baud in BAUD_RATES:
        for cfg in RTU_SERIAL_CONFIGS:
            combos.append(("rtu", baud, cfg))
        for cfg in ASCII_SERIAL_CONFIGS:
            combos.append(("ascii", baud, cfg))

    for framing, baud, (bytesize, parity, stopbits) in combos:
        framer = FramerType.RTU if framing == "rtu" else FramerType.ASCII
        label = f"{framing.upper():5s} {baud}-{parity}-{bytesize} stopbits={stopbits}"

        client = ModbusSerialClient(
            port=PORT,
            baudrate=baud,
            parity=parity,
            bytesize=bytesize,
            stopbits=stopbits,
            framer=framer,
            timeout=DISCOVERY_TIMEOUT,
        )
        if not client.connect():
            print(f"  [{label}] could not open port at all — check PORT/cable")
            continue

        any_response = False
        for slave_id in SLAVE_ID_RANGE:
            for function in ("holding", "input"):
                try:
                    rr = _read_regs_compat(client, 0, 2, slave_id, function)
                    if rr is None:
                        continue
                    if rr.isError():
                        # Exception response = device IS there, just wrong
                        # address/function. Still a real find.
                        print(f"  [{label} slave={slave_id} fn={function}] "
                              f"EXCEPTION response (device present, wrong reg/fn): {rr}")
                        found.append((framing, baud, bytesize, parity, stopbits, slave_id))
                        any_response = True
                    else:
                        print(f"  [{label} slave={slave_id} fn={function}] "
                              f"SUCCESS — raw regs: {rr.registers}")
                        found.append((framing, baud, bytesize, parity, stopbits, slave_id))
                        any_response = True
                except Exception as e:
                    # timeout / no response — expected for most combos, not printed
                    pass

        client.close()
        if not any_response:
            print(f"  [{label}] no response across slave IDs 1-10 (silence)")

    print()
    if found:
        print(f"Found {len(found)} responding combination(s):")
        for f in found:
            print(f"  framing={f[0]} baud={f[1]} bytesize={f[2]} parity={f[3]} "
                  f"stopbits={f[4]} slave={f[5]}")
    else:
        print("No combination responded at all. Check: wiring (A/B not swapped), "
              "meter is powered, meter's comm mode is actually enabled, "
              "and consider adding more baud rates to BAUD_RATES.")
    return found


def decode_all(regs):
    """Return every plausible interpretation of a 2-register pair."""
    if len(regs) < 2:
        return {}
    hi, lo = regs[0], regs[1]
    out = {}
    out["uint16[0]"] = hi
    out["uint16[1]"] = lo
    out["int16[0]"] = hi - 65536 if hi > 32767 else hi
    out["int16[1]"] = lo - 65536 if lo > 32767 else lo
    try:
        out["uint32_hilo"] = (hi << 16) | lo
        out["uint32_lohi"] = (lo << 16) | hi
        out["float32_hilo"] = struct.unpack(">f", struct.pack(">HH", hi, lo))[0]
        out["float32_lohi"] = struct.unpack(">f", struct.pack(">HH", lo, hi))[0]
    except Exception:
        pass
    return out


def phase2_scan(framing, baud, bytesize, parity, stopbits, slave_id):
    print()
    print("=" * 70)
    print(f"PHASE 2 — REGISTER SCAN using framing={framing} baud={baud} "
          f"{bytesize}-{parity}-{stopbits} slave={slave_id}")
    print(f"Scanning addresses {SCAN_ADDRESS_START}-{SCAN_ADDRESS_END}, "
          f"function codes 03 (holding) and 04 (input)")
    print("=" * 70)
    print("Compare the values below against what the meter's OWN DISPLAY "
          "is showing right now for instantaneous flow and totalizer.\n")

    framer = FramerType.RTU if framing == "rtu" else FramerType.ASCII
    client = ModbusSerialClient(
        port=PORT, baudrate=baud, parity=parity, bytesize=bytesize,
        stopbits=stopbits, framer=framer, timeout=SCAN_TIMEOUT,
    )
    if not client.connect():
        print("Could not connect for Phase 2 — check PORT.")
        return

    for function in ("holding", "input"):
        print(f"\n--- function code {'03 holding' if function=='holding' else '04 input'} ---")
        addr = SCAN_ADDRESS_START
        while addr < SCAN_ADDRESS_END:
            try:
                rr = _read_regs_compat(client, addr, 2, slave_id, function)
                if rr is None or rr.isError():
                    addr += 1
                    continue
                d = decode_all(rr.registers)
                print(f"  addr={addr:4d}  raw={rr.registers}  "
                      f"u32hilo={d.get('uint32_hilo')}  "
                      f"f32hilo={d.get('float32_hilo'):.4f}  "
                      f"f32lohi={d.get('float32_lohi'):.4f}")
            except Exception:
                pass
            addr += 1
            time.sleep(0.02)  # small gap to avoid hammering the bus

    client.close()


def main():
    found = phase1_discover()
    if not found:
        return

    print()
    print("If you see one clear winning combination above, Phase 2 will "
          "run automatically using the FIRST one found.")
    print("If multiple combos responded, the first is not necessarily "
          "correct — re-run manually with phase2_scan(...) using a "
          "different entry from the list if the scan below looks wrong.\n")

    first = found[0]
    phase2_scan(*first)


if __name__ == "__main__":
    main()
