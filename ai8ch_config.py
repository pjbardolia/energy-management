from pymodbus.client import ModbusSerialClient
import time

PORT = "/dev/tty.usbserial-A50285BI"

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

# --- Step A: confirm module responds at default address 1, 9600-N-8 ---
print("Checking default address 1 at 9600-N-8...")
version = read_reg(0x8000, 1, 1)
if version:
    print(f"  Found it. Software version: {version}")
else:
    print("  No response — check wiring/power before continuing.")
    client.close()
    exit()

# --- Step B: read current UART setting for reference ---
uart_setting = read_reg(0x2000, 1, 1)
print(f"Current UART setting (0x2000): {uart_setting}")

# --- Step C: explicitly (re)confirm baud=9600, parity=none ---
# High byte = parity (0x00 = none, safe regardless of which doc version is right)
# Low byte = baud code (0x01 = 9600)
BAUD_CODE = 0x01      # 9600
PARITY_CODE = 0x00    # none
uart_value = (PARITY_CODE << 8) | BAUD_CODE

print(f"\nSetting UART to 9600-None (register value 0x{uart_value:04X})...")
ok_uart = write_reg(0x2000, uart_value, 0)   # broadcast address
print("  Write success:", ok_uart)

time.sleep(0.5)

# --- Step D: set device address to 2 (broadcast) ---
print("\nSetting device address to 2...")
ok_addr = write_reg(0x4000, 2, 0)   # broadcast address
print("  Write success:", ok_addr)

time.sleep(0.5)

# --- Step E: verify everything at the NEW address ---
print("\nVerifying...")
check_new = read_reg(0x8000, 1, 2)
check_old = read_reg(0x8000, 1, 1)
print(f"  Response at address 2: {check_new}")
print(f"  Response at address 1: {check_old}  (should be None now)")

client.close()
