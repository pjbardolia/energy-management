from pymodbus.client import ModbusSerialClient
from pymodbus.framer import FramerType
import struct
import time

PORT = "/dev/tty.usbserial-A50285BI"
SLAVE_ID = 2

client = ModbusSerialClient(
    port=PORT, baudrate=9600, parity="O", stopbits=1, bytesize=7,
    timeout=1, framer=FramerType.ASCII,
)

if not client.connect():
    print("Could not open port.")
else:
    print("Connected. Reading flow (2001, float) and totalizer (2007 Long / 2009 float)...\n")
    try:
        while True:
            try:
                # Flow — genuinely a float per the manual
                r_flow = client.read_holding_registers(address=2001, count=2, device_id=SLAVE_ID)
                flow_raw = r_flow.registers
                flow_val = struct.unpack('>f', struct.pack('>HH', *flow_raw))[0]

                # Totalizer integer part — a Long (32-bit unsigned int), NOT a float
                r_int = client.read_holding_registers(address=2007, count=2, device_id=SLAVE_ID)
                int_raw = r_int.registers
                total_int = (int_raw[0] << 16) | int_raw[1]

                # Totalizer fraction part — genuinely a float
                r_frac = client.read_holding_registers(address=2009, count=2, device_id=SLAVE_ID)
                frac_raw = r_frac.registers
                total_frac = struct.unpack('>f', struct.pack('>HH', *frac_raw))[0]

                total = total_int + total_frac

                print(f"Flow: {flow_val:.4f}   Totalizer: {total_int} + {total_frac:.4f} = {total:.4f} L")
            except Exception as e:
                print("Read error:", e)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.close()
