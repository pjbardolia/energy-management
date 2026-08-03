from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusIOException
import time

PORT = "/dev/tty.usbserial-A50285BI"
BAUD = 9600
SLAVE_ID = 6   # Jet 07

client = ModbusSerialClient(
    port=PORT, baudrate=BAUD, parity="N", stopbits=1, bytesize=8, timeout=1
)

if not client.connect():
    print("Could not open port.")
else:
    print(f"Connected. Polling Jet 07 (slave {SLAVE_ID}) at 0x3000...")
    try:
        while True:
            try:
                result = client.read_holding_registers(address=0x3000, count=8, device_id=SLAVE_ID)
                if result.isError():
                    print("Modbus error response:", result)
                else:
                    print("Raw registers:", result.registers)
            except ModbusIOException as e:
                print("No response (timeout):", e)
            except Exception as e:
                print("Other error:", e)
            time.sleep(2)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        client.close()
