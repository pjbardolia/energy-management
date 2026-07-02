from pymodbus.client import ModbusSerialClient
import time

client = ModbusSerialClient(
    port='/dev/ttyUSB2',
    baudrate=9600,
    parity='N',
    stopbits=1,
    bytesize=8,
    timeout=1
)

if client.connect():
    print("Connected to VFD")

    while True:

        rr = client.read_holding_registers(
            address=0x3000,
            count=8,
            device_id=1
        )

        if not rr.isError():

            freq = rr.registers[0]/100
            ref_freq = rr.registers[1]/100
            dc_voltage = rr.registers[2]/10
            output_voltage = rr.registers[3]
            current = rr.registers[4]/10
            rpm = rr.registers[5]
            power = rr.registers[6]/10
            torque = rr.registers[7]/10

            print("--------------------")
            print(f"Frequency      : {freq:.2f} Hz")
            print(f"Reference freq : {ref_freq:.2f} Hz")
            print(f"DC Bus Voltage : {dc_voltage:.1f} V")
            print(f"Output Voltage : {output_voltage} V")
            print(f"Current        : {current:.1f} A")
            print(f"Speed          : {rpm} RPM")
            print(f"Power          : {power:.1f}")
            print(f"Torque         : {torque:.1f} %")

        else:
            print("Modbus error")

        time.sleep(1)

else:
    print("Connection failed")
