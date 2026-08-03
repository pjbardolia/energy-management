from pymodbus.client import ModbusSerialClient
import time

PORT = "/dev/tty.usbserial-A50285BI"
SLAVE_ID = 2   # AI8CH module's confirmed address, set in the previous step

client = ModbusSerialClient(
    port=PORT, baudrate=9600, parity="N", stopbits=1, bytesize=8, timeout=1
)

if not client.connect():
    print("Could not open port.")
    exit()

def read_reg(addr, count, slave_id):
    try:
        result = client.read_holding_registers(address=addr, count=count, device_id=slave_id)
        if result.isError():
            return None
        return result.registers
    except Exception as e:
        print("Read error:", e)
        return None

def write_reg(addr, value, slave_id):
    try:
        result = client.write_register(address=addr, value=value, device_id=slave_id)
        return not result.isError()
    except Exception as e:
        print("Write error:", e)
        return False

# --- Step A: confirm module still responds at address 2 ---
print(f"Checking module at address {SLAVE_ID}...")
version = read_reg(0x8000, 1, SLAVE_ID)
if version:
    print(f"  Found it. Software version register: {version}")
else:
    print("  No response — check wiring/power before continuing.")
    client.close()
    exit()

# --- Step B: read AI1's current mode setting, before changing anything ---
current_mode = read_reg(0x1000, 1, SLAVE_ID)
print(f"AI1 current mode (0x1000): {current_mode}")

# --- Step C: set AI1 to 4-20mA mode (0x0003) ---
print("\nSetting AI1 to 4-20mA mode...")
ok_mode = write_reg(0x1000, 0x0003, SLAVE_ID)
print("  Write success:", ok_mode)

time.sleep(0.5)

# --- Step D: verify ---
mode_check = read_reg(0x1000, 1, SLAVE_ID)
print(f"AI1 mode readback: {mode_check}  (should be [3])")

client.close()
