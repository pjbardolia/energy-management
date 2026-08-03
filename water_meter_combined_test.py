#!/usr/bin/env python3
"""
Combined test: Eureka manual's confirmed-document addresses (2001/2007/2009,
FC03) vs. the generic L-MAG OEM addresses (0x1010/0x1021/0x1024, FC04) —
neither has ever actually been read from real hardware yet. Testing both
now that the dongle itself is validated against a known-good VFD.
"""

import time
import struct
import logging

from pymodbus.client import ModbusSerialClient

SERIAL_PORT = "/dev/tty.usbserial-A50285BI"
BAUD = 9600
PARITY = "O"
BYTESIZE = 7
SLAVE_ID = 2   # Jet 11 — adjust to 1 to also try Jet 12

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("combined_test")

# (label, function_code, address, register_count)
TESTS = [
    ("Eureka manual: instantaneous flow (FC03)",   3, 2001,   2),
    ("Eureka manual: cumulative integer (FC03)",   3, 2007,   2),
    ("Eureka manual: cumulative fraction (FC03)",  3, 2009,   2),
    ("L-MAG generic: instantaneous flow (FC04)",   4, 0x1010, 2),
    ("L-MAG generic: cumulative total unit (FC04)",4, 0x1021, 1),
    ("L-MAG generic: alarm status (FC04)",         4, 0x1024, 1),
]


def try_read(client, fc, addr, count, slave_id):
    try:
        if fc == 3:
            result = client.read_holding_registers(address=addr, count=count, device_id=slave_id)
        else:
            result = client.read_input_registers(address=addr, count=count, device_id=slave_id)
        if result is None or result.isError():
            return None
        return result.registers
    except Exception as e:
        return None


def main():
    client = ModbusSerialClient(
        port=SERIAL_PORT, baudrate=BAUD, parity=PARITY,
        stopbits=1, bytesize=BYTESIZE, timeout=1, retries=1,
    )

    if not client.connect():
        log.error(f"Could not open {SERIAL_PORT}")
        return

    log.info(f"Connected. Testing slave {SLAVE_ID} at {BAUD}-{PARITY}-{BYTESIZE}\n")

    any_hit = False
    for label, fc, addr, count in TESTS:
        regs = try_read(client, fc, addr, count, SLAVE_ID)
        if regs is not None:
            log.info(f"[HIT] {label}  addr=0x{addr:04X}  raw={regs}")
            if count == 2:
                try:
                    raw = struct.pack('>HH', regs[0], regs[1])
                    as_float = struct.unpack('>f', raw)[0]
                    log.info(f"       -> as float: {as_float}")
                except Exception:
                    pass
            any_hit = True
        else:
            log.warning(f"[no response] {label}  addr=0x{addr:04X}")
        time.sleep(0.3)

    client.close()
    log.info("\n" + "=" * 60)
    if not any_hit:
        log.info("Zero hits across BOTH address schemes and BOTH function codes.")
        log.info("This is a strong result: it's very unlikely to be a register")
        log.info("problem anymore — points back to physical connection.")
    else:
        log.info("At least one address/function code combo responded — that's")
        log.info("your real register map, use it going forward.")


if __name__ == "__main__":
    main()
