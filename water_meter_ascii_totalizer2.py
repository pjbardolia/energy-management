from pymodbus.client import ModbusSerialClient
from pymodbus.framer import FramerType
import struct
import time

PORT = "/dev/tty.usbserial-A50285BI"
SLAVE_ID = 2  # Device Address 001, confirmed on meter's screen

client = ModbusSerialClient(
    port=PORT,
    baudrate=9600,
    parity="O",
    stopbits=1,
    bytesize=7,
    timeout=1,
    framer=FramerType.ASCII,
)

if not client.connect():
    print("Could not open port.")
else:
    print("Connected. Reading flow (2001) and totalizer (2007/2009), ASCII framing...\n")
    try:
        while True:
            for label, addr in [
                ("Flow (2001)", 2001),
                ("Totalizer integer part (2007)", 2007),
                ("Totalizer fraction part (2009)", 2009),
            ]:
                try:
                    r = client.read_holding_registers(address=addr, count=2, device_id=SLAVE_ID)
                    if r.isError():
                        print(f"{label}: error {r}")
                    else:
                        regs = r.registers
                        raw = struct.pack('>HH', regs[0], regs[1])
                        as_float = struct.unpack('>f', raw)[0]
                        print(f"{label}: raw={regs}  as_float={as_float}")
                except Exception as e:
                    print(f"{label}: exception {e}")
            print("---")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.close()
