#!/usr/bin/env python3
"""
Eureka EUMAG water meter — direct bench test via USB-RS485 dongle.
Bypasses the 90m panel cable entirely, using a short test lead straight
to the meter, to isolate device/settings issues from cable issues.

Confirmed settings:
  - Baud: 9600
  - Parity: Odd (locked on meter panel)
  - Data bits: 7
  - Slave ID: 2 (Jet 11)
  - Register: 2001 decimal (0x07D1), FC03 holding registers, float
"""

import time
import struct
import logging

from pymodbus.client import ModbusSerialClient

# --- config ---
SERIAL_PORT = "/dev/tty.usbserial-A50285BI"   # your HW-157 dongle - update if different
BAUD_RATE = 9600
PARITY = "O"
BYTESIZE = 7
SLAVE_ID = 2                                   # Jet 11
FLOW_REGISTER = 2001

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("watermeter_bench")


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


def try_read_flow(client, slave_id, reg_address):
    result = _call_read(client, reg_address, 2, slave_id)
    if result is None:
        return None
    try:
        regs = result.registers
        raw = struct.pack('>HH', regs[0], regs[1])
        value = struct.unpack('>f', raw)[0]
        return value, regs
    except Exception:
        return None


def main():
    client = ModbusSerialClient(
        port=SERIAL_PORT,
        baudrate=BAUD_RATE,
        parity=PARITY,
        stopbits=1,
        bytesize=BYTESIZE,
        timeout=1,
        retries=1,
    )

    if not client.connect():
        log.error(f"Could not open {SERIAL_PORT}")
        return

    log.info(f"Connected. Polling slave {SLAVE_ID}, register {FLOW_REGISTER} "
              f"at {BAUD_RATE}-{PARITY}-{BYTESIZE} on {SERIAL_PORT} ...")
    log.info("Press Ctrl+C to stop.\n")

    try:
        while True:
            outcome = try_read_flow(client, SLAVE_ID, FLOW_REGISTER)
            if outcome:
                value, raw_regs = outcome
                log.info(f"Instantaneous flow: {value:.4f}  raw={raw_regs}")
            else:
                log.warning("No valid response this poll")
            time.sleep(2)
    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
