# Factory Domain Knowledge

## Company

Name:
Shiv Shakti Prints Pvt Ltd

Industry:
Textile Dyeing and Processing

Location:
Surat, Gujarat, India

---

## Factory Layout

### Jet Dyeing Machines

Total Machines:
33

Examples:
Jet 1
Jet 2
Jet 33

Purpose:
Fabric dyeing

---

### Stenters

Total Machines:
3

Purpose:
Fabric finishing and drying

---

## Utilities

### Thermic Fluid Heater

Purpose:
Heat generation for dyeing and finishing operations

---

## Electrical Infrastructure

### VFD

Model:
INVT CHF100A

Communication:
Modbus RTU

Interface:
RS485

Baud Rate:
9600

Protocol:
8N1

---

## IoT Architecture

Factory Equipment
        |
        v
VFD / Sensors
        |
        v
RS485 Network
        |
        v
USR-N540 RS485 Gateway
        |
        v
Raspberry Pi
        |
        v
FastAPI Backend
        |
        v
PostgreSQL Database
        |
        v
Dashboard / Mobile App

---

## Telemetry Examples

Electrical:
- Voltage
- Current
- Frequency
- Power
- Energy

Production:
- Production Meter
- Batch Meter

Utilities:
- Water Consumption
- Temperature
- Pressure

---

## Future Goals

- Energy Monitoring
- Production Monitoring
- Water Monitoring
- Predictive Maintenance
- Alarm Management
- Mobile App
- Multi-Company SaaS

