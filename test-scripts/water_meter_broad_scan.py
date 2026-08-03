#!/usr/bin/env python3
"""
Broad bench scan for Eureka flowmeter via USB-RS485 dongle.
Sweeps baud rate, parity, byte size, and slave ID to find ANY
combination that gets a real response, rather than assuming
the panel-confirmed settings still apply on this connection.
"""

import time
import struct
import logging

from pymodbus.client import ModbusSerialClient

SERIAL_PORT = "/dev/tty.usbserial-A50285BI"

BAUD_RATES = [9600, 19200, 4800, 38400]
PARITIES = ["O", "N", "E"]
BYTESIZES = [7, 8]
SLAVE_IDS = range(1, 11)
FLOW_REGISTER = 2001

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("broad_scan")


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
    hits = []
    total_combos = len(BAUD_RATES) * len(PARITIES) * len(BYTESIZES)
    combo_num = 0

    for baud in BAUD_RATES:
        for parity in PARITIES:
            for bytesize in BYTESIZES:
                combo_num += 1
                log.info(f"[{combo_num}/{total_combos}] Trying {baud}-{parity}-{bytesize}...")

                client = ModbusSerialClient(
                    port=SERIAL_PORT, baudrate=baud, parity=parity,
                    stopbits=1, bytesize=bytesize, timeout=0.4, retries=0,
                )
                if not client.connect():
                    log.warning(f"  Could not open port at {baud}-{parity}-{bytesize}")
                    continue

                for slave_id in SLAVE_IDS:
                    outcome = try_read_flow(client, slave_id, FLOW_REGISTER)
                    if outcome:
                        value, raw_regs = outcome
                        log.info(f"  [HIT] slave={slave_id} baud={baud} parity={parity} "
                                  f"bytesize={bytesize}  FLOW={value:.4f}  raw={raw_regs}")
                        hits.append((baud, parity, bytesize, slave_id, value, raw_regs))
                    time.sleep(0.03)

                client.close()

    log.info("=" * 60)
    log.info(f"Scan complete. {len(hits)} hits across {total_combos} baud/parity/bytesize "
              f"combos x {len(list(SLAVE_IDS))} slave IDs.")
    if not hits:
        log.info("No response under ANY combination. This points strongly toward a")
        log.info("physical connection problem (A/B swapped or not making contact,")
        log.info("meter not actually powered, or wrong physical device) rather than")
        log.info("a settings mismatch — since we just tried everything reasonable.")
    else:
        for h in hits:
            log.info(f"  baud={h[0]} parity={h[1]} bytesize={h[2]} slave={h[3]} -> FLOW={h[4]:.4f}")


if __name__ == "__main__":
    main()
