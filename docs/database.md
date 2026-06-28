# Database Schema

## Overview

Company
 ├── Users
 ├── Departments
 │     └── Machines
 │            └── Machine Component Instances
 │                    └── Telemetry Tags
 │                            └── Telemetry Data

---

## company

Purpose:
Represents a tenant/company.

Relationships:
- Has many users
- Has many departments

---

## users

Purpose:
Application users.

Examples:
- Admin
- Supervisor
- Operator

Relationships:
- Belongs to company

---

## department

Purpose:
Logical factory department.

Examples:
- Dyeing
- Finishing
- Printing
- Utilities

Relationships:
- Belongs to company
- Has many machines

---

## machine_type

Purpose:
Template for machine categories.

Examples:
- Jet Dyeing Machine
- Stenter
- Boiler
- Thermic Fluid Heater

---

## machine

Purpose:
Physical machine instance.

Examples:
- Jet 33
- Jet 12
- Stenter 2

Relationships:
- Belongs to department
- Belongs to machine type

---

## component_type

Purpose:
Template for machine components.

Examples:
- Main Motor
- Pump
- Fan
- Blower

---

## component_type_tag

Purpose:
Defines available telemetry tags for a component type.

Examples:
- Current
- Voltage
- Frequency
- Power
- Run Hours

---

## machine_component_instance

Purpose:
Actual installed component on a machine.

Example:
Jet 33 -> Main Pump

Relationships:
- Belongs to machine
- Belongs to component type

---

## tag_definition

Purpose:
Defines telemetry tags.

Examples:
- Current
- Voltage
- Frequency
- Temperature
- Pressure

---

## telemetry_data

Purpose:
Stores time-series telemetry readings.

Examples:
Current = 12.3 A
Voltage = 415 V
Power = 8.4 kW

---

## data

Purpose:
Legacy telemetry table.
To be reviewed for migration.

