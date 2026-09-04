#!/usr/bin/env python3
"""
AI8CH channel-map probe — Jet 25 / Jet 26 pressure transmitter wiring.

Jet 27's existing Waveshare AI8CH module (slave_id=2, bus /dev/ttyUSB0) is
being reused to also carry Jet 25 and Jet 26's new pressure transmitters
(channels 6 and 7, per the physical wiring — CH2-CH5 are NOT wired yet).
gateway_service.py's read_ai8ch_pressure() currently only ever reads
register 0x0000 (hardcoded, no channel concept at all) — this script reads
a WIDE range of registers on the SAME slave so we can watch, live, which
register actually moves when a transmitter is connected/disconnected,
exactly the same "match against real ground truth" method used to find
the Jet 11 flowmeter's totalizer register — NOT a guess from a datasheet.

Connection settings match read_ai8ch_pressure()'s bus EXACTLY (see
config.json buses[2] / gateway_service.py's _poll_bus() defaults for this
bus: no "framer"/"bytesize" override there, so RTU + 8 data bits, same as
every non-EU1000 bus) — so a register that responds here will respond
identically in production.

READ-ONLY / production-safe: function code 04 (read_input_registers) only,
never a write. Safe to run repeatedly against the live gateway's serial
port while the actual gateway service is between polls, or safe to run
standalone against a spare USB-RS485 adapter cabled to the same bus.

HOW TO USE
  1. Update PORT below if running against a different adapter path than
     the Pi's /dev/ttyUSB0 (e.g. a Mac's /dev/tty.usbserial-XXXX).
  2. Run it, then physically connect/disconnect (or otherwise perturb)
     Jet 25's transmitter, then Jet 26's, one at a time, watching for
     which register's value visibly changes (a "*** CHANGED ***" marker
     is printed next to any register whose value differs from the
     previous poll).
  3. Ctrl+C to stop.
"""

import time

from pymodbus.client import ModbusSerialClient
from pymodbus.framer import FramerType

# ---------------------------------------------------------------------
PORT     = "/dev/ttyUSB0"   # Jet 27 Temp + Pressure bus — update if testing elsewhere
BAUD     = 9600
PARITY   = "N"
BYTESIZE = 8
STOPBITS = 1
FRAMER   = FramerType.RTU   # this bus has no "framer" override in config.json -> RTU default

SLAVE_ID = 2                # same slave as Jet 27 Pressure (config.json: WAVESHARE_AI8CH)

SCAN_START = 0x0000
SCAN_END   = 0x0010          # inclusive — registers 0x0000 through 0x0010

POLL_INTERVAL_SEC = 1.0

# Same conversion read_ai8ch_pressure() uses, for context only (not assumed
# to apply to every register — printed for whichever register looks like a
# 4-20mA channel, i.e. raw/1000 lands in a sane mA range).
MA_MIN, MA_MAX = 4.0, 20.0
RANGE_KGCM2 = 6.0 * 1.01972
# ---------------------------------------------------------------------


def as_pressure_hint(raw: int) -> str:
    """If this raw value looks like a 4-20mA reading (in microamps, same
    scale read_ai8ch_pressure() assumes), show what it'd decode to — purely
    a hint to eyeball, not a claim that this register IS a pressure channel."""
    current_ma = raw / 1000.0
    if not (0.0 <= current_ma <= 24.0):
        return ""
    ma_clamped = max(current_ma, MA_MIN)
    kgcm2 = (ma_clamped - MA_MIN) / (MA_MAX - MA_MIN) * RANGE_KGCM2
    return f"  (~{current_ma:.3f} mA -> ~{kgcm2:.3f} kg/cm² if this is a 4-20mA channel)"


def main():
    client = ModbusSerialClient(
        port=PORT, baudrate=BAUD, parity=PARITY, bytesize=BYTESIZE,
        stopbits=STOPBITS, framer=FRAMER, timeout=1,
    )
    if not client.connect():
        print(f"Could not open {PORT}.")
        return

    addresses = list(range(SCAN_START, SCAN_END + 1))
    print(f"Connected to {PORT} (slave_id={SLAVE_ID}, RTU, {BAUD} 8{PARITY}1).")
    print(f"Polling input registers 0x{SCAN_START:04X}-0x{SCAN_END:04X} every "
          f"{POLL_INTERVAL_SEC}s. Press Ctrl+C to stop.\n")
    print("Now: leave everything as-is for a few polls to see the baseline, "
          "then connect/disconnect Jet 25's transmitter, then Jet 26's, one "
          "at a time, watching for a '*** CHANGED ***' marker.\n")

    last_values = {}

    try:
        while True:
            print(f"--- poll @ {time.strftime('%H:%M:%S')} ---")
            for addr in addresses:
                try:
                    rr = client.read_input_registers(address=addr, count=1, device_id=SLAVE_ID)
                except Exception as exc:
                    print(f"  0x{addr:04X}: read error — {exc}")
                    continue

                if rr.isError():
                    print(f"  0x{addr:04X}: error response — {rr}")
                    continue

                raw = rr.registers[0]
                changed = addr in last_values and last_values[addr] != raw
                marker = "  *** CHANGED ***" if changed else ""
                print(f"  0x{addr:04X}: {raw:6d}{as_pressure_hint(raw)}{marker}")
                last_values[addr] = raw

            print()
            time.sleep(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
