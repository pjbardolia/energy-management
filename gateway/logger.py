from pymodbus.client import ModbusSerialClient
import time
import csv
from datetime import datetime

client = ModbusSerialClient(
    port='/dev/ttyUSB0',
    baudrate=9600,
    parity='N',
    stopbits=1,
    bytesize=8,
    timeout=1
)

with open('vfd_log.csv', 'a', newline='') as file:

    writer = csv.writer(file)

    if client.connect():

        while True:

            rr = client.read_holding_registers(
                address=0x3000,
                count=8,
                device_id=1
            )

            if not rr.isError():

                timestamp = datetime.now()

                freq = rr.registers[0]/100
                ref_freq = rr.registers[1]/100
                dc_voltage = rr.registers[2]/10
                output_voltage = rr.registers[3]
                current = rr.registers[4]/10
                rpm = rr.registers[5]
                power = rr.registers[6]/10
                torque = rr.registers[7]/10

                writer.writerow([
                    timestamp,
                    freq,
                    ref_freq,
                    dc_voltage,
                    output_voltage,
                    current,
                    rpm,
                    power,
                    torque
                ])

                file.flush()

                print(timestamp,
                      freq,
                      current,
                      rpm)

            time.sleep(10)

    client.close()
