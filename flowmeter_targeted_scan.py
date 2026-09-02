#!/usr/bin/env python3
"""
Targeted + wide scan, round 2 — new (Jet 11 / Jet 12) flowmeters.

CONFIRMED FROM PHYSICAL DISPLAY (ground truth, read directly off the
meter's own screen):
  Jet 11:  instantaneous flow = 0.00 m3/h (idle), totalizer = 12365.3 m3
  Jet 12:  instantaneous flow = 0.00 m3/h (idle), totalizer = 12535414.0 L
           (Arrowmech AEFM-100/AEMF-100, DN50, K=0.12659)

LEAD: several similarly-specced Chinese-OEM electromagnetic flowmeters
(sold rebranded as L-MAG, RIF100, Microsensor MFE600, Armstrong AMF,
Holykell 4800E) use register 4112 decimal (0x1010), function code 04
(input registers), float32, for instantaneous flow rate. This is a
PATTERN across similar products, NOT a confirmed Arrowmech document —
treat as a hypothesis to test, not settled fact.

Since flow is currently 0.00 on both meters, matching flow alone is
weak evidence (many registers read 0 when idle). The TOTALIZER is a
much better anchor — it's large and distinctive. This script
automatically flags any register whose decoded value is close to the
known totalizer, across BOTH function codes and a wide address range,
so you don't have to eyeball thousands of printed lines.

Update TOTALIZER_TARGET below for whichever meter you're currently
scanning (Jet 11 = 12365.3, Jet 12 = 12535414.0) before running.
"""

import struct
import time

from pymodbus.client import ModbusSerialClient
from pymodbus.framer import FramerType

# ---------------------------------------------------------------------
PORT = "/dev/ttyUSB2"          # update per which meter's port you're on
FRAMING = "rtu"                 # confirmed for Jet 11 last round
BAUD = 9600
BYTESIZE, PARITY, STOPBITS = 8, "N", 1
SLAVE_ID = 1                    # confirmed responsive last round

# SET THIS to whichever meter you're currently scanning:
TOTALIZER_TARGET = 12365.3      # Jet 11 = 12365.3 (m3) | Jet 12 = 12535414.0 (L)
TOTALIZER_TOLERANCE_PCT = 2.0   # how close counts as a match (%)

TARGETED_PROBE_CENTER = 4112    # the common-OEM lead (0x1010)
TARGETED_PROBE_SPAN = 40        # check +/- this many addresses around it

WIDE_SCAN_START = 0
WIDE_SCAN_END = 5000
# ---------------------------------------------------------------------


def _read_regs_compat(client, address, count, slave, function):
    fn = client.read_holding_registers if function == "holding" else client.read_input_registers
    try:
        return fn(address=address, count=count, slave=slave)
    except TypeError:
        return fn(address=address, count=count, device_id=slave)


def decode_all(regs):
    if len(regs) < 2:
        return {}
    hi, lo = regs[0], regs[1]
    out = {}
    try:
        out["uint32_hilo"] = (hi << 16) | lo
        out["uint32_lohi"] = (lo << 16) | hi
        out["float32_hilo"] = struct.unpack(">f", struct.pack(">HH", hi, lo))[0]
        out["float32_lohi"] = struct.unpack(">f", struct.pack(">HH", lo, hi))[0]
    except Exception:
        pass
    return out


def is_close_to_target(value, target, pct):
    if target == 0:
        return abs(value) < 0.01
    return abs(value - target) / abs(target) * 100 <= pct


def check_and_report(addr, function, regs):
    d = decode_all(regs)
    matches = []
    for key in ("uint32_hilo", "uint32_lohi", "float32_hilo", "float32_lohi"):
        val = d.get(key)
        if val is not None and is_close_to_target(val, TOTALIZER_TARGET, TOTALIZER_TOLERANCE_PCT):
            matches.append(f"{key}={val}")
    if matches:
        print(f"  *** POSSIBLE TOTALIZER MATCH *** addr={addr} fn={function} "
              f"raw={regs}  MATCHES: {', '.join(matches)}")
        return True
    return False


def targeted_probe(client):
    print("=" * 70)
    print(f"TARGETED PROBE - common-OEM register {TARGETED_PROBE_CENTER} "
          f"(0x{TARGETED_PROBE_CENTER:04X}) +/- {TARGETED_PROBE_SPAN}, function 04 input")
    print("=" * 70)
    any_data = False
    for addr in range(TARGETED_PROBE_CENTER - TARGETED_PROBE_SPAN,
                       TARGETED_PROBE_CENTER + TARGETED_PROBE_SPAN):
        try:
            rr = _read_regs_compat(client, addr, 2, SLAVE_ID, "input")
            if rr is None or rr.isError():
                continue
            any_data = True
            d = decode_all(rr.registers)
            print(f"  addr={addr:5d}  raw={rr.registers}  "
                  f"f32hilo={d.get('float32_hilo'):.4f}  f32lohi={d.get('float32_lohi'):.4f}")
            check_and_report(addr, "input", rr.registers)
        except Exception:
            pass
        time.sleep(0.02)
    if not any_data:
        print("  No data anywhere in this range via function 04 - OEM lead didn't pan out here.")
    print()


def wide_scan(client):
    print("=" * 70)
    print(f"WIDE SCAN - addresses {WIDE_SCAN_START}-{WIDE_SCAN_END}, both function codes")
    print(f"Auto-flagging anything within {TOTALIZER_TOLERANCE_PCT}% of "
          f"totalizer target {TOTALIZER_TARGET}")
    print("(Only matches are printed - this will be quiet if nothing matches "
          "for long stretches, that's normal)")
    print("=" * 70)

    match_count = 0
    for function in ("holding", "input"):
        addr = WIDE_SCAN_START
        while addr < WIDE_SCAN_END:
            try:
                rr = _read_regs_compat(client, addr, 2, SLAVE_ID, function)
                if rr is not None and not rr.isError():
                    if check_and_report(addr, function, rr.registers):
                        match_count += 1
            except Exception:
                pass
            addr += 1
            time.sleep(0.015)

    print(f"\nWide scan complete. {match_count} potential match(es) found above.")
    if match_count == 0:
        print("No matches at all - consider: totalizer might be stored in a "
              "non-float format (e.g. straight uint32 in different units), "
              "or the address range needs to go even wider, or a different "
              "slave ID/function code combination.")


def main():
    framer = FramerType.RTU if FRAMING == "rtu" else FramerType.ASCII
    client = ModbusSerialClient(
        port=PORT, baudrate=BAUD, parity=PARITY, bytesize=BYTESIZE,
        stopbits=STOPBITS, framer=framer, timeout=0.6,
    )
    if not client.connect():
        print(f"Could not connect to {PORT}")
        return

    print(f"Connected. Scanning for totalizer target = {TOTALIZER_TARGET}\n")

    targeted_probe(client)
    wide_scan(client)

    client.close()


if __name__ == "__main__":
    main()
