from pymodbus.client import ModbusSerialClient
import time

PORT = "/dev/tty.usbserial-A50285BI"
SLAVE_ID = 2

# Transmitter's rated range, from its nameplate
RANGE_BAR = 6.0
RANGE_KGCM2 = RANGE_BAR * 1.01972   # ~6.118 kg/cm²

MA_MIN = 4.0
MA_MAX = 20.0

client = ModbusSerialClient(
    port=PORT, baudrate=9600, parity="N", stopbits=1, bytesize=8, timeout=1
)

if not client.connect():
    print("Could not open port.")
    exit()

print(f"Connected. Polling AI1 on slave {SLAVE_ID}. Range: 0-{RANGE_KGCM2:.3f} kg/cm² (0-{RANGE_BAR} bar)")
print("Press Ctrl+C to stop.\n")

try:
    while True:
        try:
            result = client.read_input_registers(address=0x0000, count=1, device_id=SLAVE_ID)
            if result.isError():
                print("Error response:", result)
            else:
                raw = result.registers[0]
                current_ma = raw / 1000.0

                # Clamp slightly below 4mA (sensor noise) to avoid negative pressure display
                ma_clamped = max(current_ma, MA_MIN)
                pressure_kgcm2 = (ma_clamped - MA_MIN) / (MA_MAX - MA_MIN) * RANGE_KGCM2
                pressure_bar = (ma_clamped - MA_MIN) / (MA_MAX - MA_MIN) * RANGE_BAR

                print(f"{current_ma:.3f} mA  ->  {pressure_kgcm2:.3f} kg/cm²  ({pressure_bar:.3f} bar)")
        except Exception as e:
            print("Read error:", e)
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    client.close()
