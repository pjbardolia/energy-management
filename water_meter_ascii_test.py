from pymodbus.client import ModbusSerialClient
from pymodbus.framer import FramerType
import time

PORT = "/dev/tty.usbserial-A50285BI"
SLAVE_ID = 2  # Device Address = 001, confirmed on screen

client = ModbusSerialClient(
    port=PORT,
    baudrate=9600,
    parity="O",
    stopbits=1,
    bytesize=7,
    timeout=1,
    framer=FramerType.ASCII,   # <-- the missing piece this whole time
)

if not client.connect():
    print("Could not open port.")
else:
    print(f"Connected. Polling slave {SLAVE_ID} at register 2001 (ASCII framing)...")
    try:
        while True:
            try:
                result = client.read_holding_registers(address=2001, count=2, device_id=SLAVE_ID)
                if result.isError():
                    print("Error response:", result)
                else:
                    print("Raw registers:", result.registers)
            except Exception as e:
                print("Read error:", e)
            time.sleep(2)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        client.close()
