#!/usr/bin/env python3
"""
Eureka EUMAG water meter — totalizer bench test via USB-RS485 dongle.
Reads confirmed registers 2007 (integer part) and 2009 (fraction part),
sums them, and checks against the meter's known physical display reading.
"""

import struct
import logging

from pymodbus.client import ModbusSerialClient

SERIAL_PORT = "/dev/tty.usbserial-A50285BI"   # update if different
BAUD = 9600
PARITY = "O"
BYTESIZE = 7
SLAVE_ID = 2   # Jet 11

# Update this to whatever the meter's physical display currently shows, in liters
KNOWN_TOTAL_LITERS = 45316078.89   # Jet 11 reading from earlier this week - update to current value
TOLERANCE_PCT = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("totalizer_bench")


def _call_read(client, addr, count, slave_id):
    attempts = [
        lambda: client.read_holding_registers(address=addr, count=count, device_id=slave_id),
        lambda: client.read_holding_registers(address=addr, count=count, slave=slave_id),
        lambda: client.read_holding_registers(address=addr, count=count, unit=slave_id),
        lambda: client.read_holding_registers(addr, count, slave_id),
    ]
    for attempt in attempts:
        try:
            result = attempt()
            if result is not None and not result.isError():
                return result
        except TypeError:
            continue
        except Exception:
            return None
    return None


def words_to_int(words, big_word_order=True):
    ordered = words if big_word_order else list(reversed(words))
    val = 0
    for w in ordered:
        val = (val << 16) | w
    return val


def main():
    client = ModbusSerialClient(
        port=SERIAL_PORT, baudrate=BAUD, parity=PARITY,
        stopbits=1, bytesize=BYTESIZE, timeout=1, retries=1,
    )

    if not client.connect():
        log.error(f"Could not open {SERIAL_PORT}")
        return

    log.info(f"Connected to {SERIAL_PORT} @ {BAUD}-{PARITY}-{BYTESIZE}. "
              f"Reading totalizer registers 2007/2009 from slave {SLAVE_ID}...")

    int_result = _call_read(client, 2007, 2, SLAVE_ID)
    frac_result = _call_read(client, 2009, 2, SLAVE_ID)

    client.close()

    if int_result is None or frac_result is None:
        log.error("Could not read one or both totalizer registers. "
                   "Check wiring/settings before assuming the register is wrong.")
        return

    int_regs = int_result.registers
    frac_regs = frac_result.registers

    int_part = words_to_int(int_regs, big_word_order=True)
    packed = struct.pack(">HH", frac_regs[0], frac_regs[1])
    frac_part = struct.unpack(">f", packed)[0]

    total = int_part + frac_part

    log.info(f"Integer part (2007): {int_part}  raw={int_regs}")
    log.info(f"Fraction part (2009): {frac_part:.4f}  raw={frac_regs}")
    log.info(f"TOTAL: {total:.4f} L")

    pct_diff = abs(total - KNOWN_TOTAL_LITERS) / KNOWN_TOTAL_LITERS * 100
    if pct_diff <= TOLERANCE_PCT:
        log.info(f"[MATCH] Within {pct_diff:.4f}% of known display value ({KNOWN_TOTAL_LITERS} L)")
    else:
        log.warning(f"[NO MATCH] {pct_diff:.4f}% off from known display value "
                     f"({KNOWN_TOTAL_LITERS} L) — check the meter's current physical reading, "
                     f"it may have simply ticked up since you last noted it")


if __name__ == "__main__":
    main()
