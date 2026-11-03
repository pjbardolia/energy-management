#!/usr/bin/env python3
"""
pi_add_temperature_meters.py — adds the 12 iTherm FX-438 temperature meters
(Jets 21-26 and 28-33) to the gateway's config.json on the Pi. Safe to re-run.

Run it ON THE PI, from the gateway folder, without sudo, AFTER the new
gateway_service.py (the one that knows ITHERM_FX438_TENTHS) is in place and
BEFORE restarting the gateway:

    cd /home/pi/gateway
    python3 pi_add_temperature_meters.py

What it does, in order:
  1. Refuses unless gateway_service.py in this folder knows ITHERM_FX438_TENTHS.
     A config naming a model the code doesn't know stops the gateway from
     starting at all (systemd retries every 10 s, with no data from any bus).
  2. Refuses unless config.json has the /dev/ttyUSB0 bus and
     tag_definition_ids["temperature"] is 8.
  3. Refuses if any of slaves 4-15 on /dev/ttyUSB0, or any of component ids
     45-56 on any bus, is already used by something else. If all 12 entries
     are already there, it says so and changes nothing.
  4. Copies config.json to config.json.bak-<date_time>, writes the new file,
     then checks it with gateway_service.load_config() — the same check the
     gateway runs at startup. If that check fails, it puts the backup back.

It never restarts the gateway: you do that after it prints OK.
"""

import datetime
import json
import os
import shutil
import sys

GATEWAY_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(GATEWAY_DIR, "config.json")
BUS_PORT = "/dev/ttyUSB0"
MODEL = "ITHERM_FX438_TENTHS"
TEMPERATURE_TAG_ID = 8

# (slave_id, jet, machine_component_instance id from migration 013)
METERS = [
    (4, "Jet 21", 45), (5, "Jet 22", 46), (6, "Jet 23", 47), (7, "Jet 24", 48),
    (8, "Jet 25", 49), (9, "Jet 26", 50), (10, "Jet 28", 51), (11, "Jet 29", 52),
    (12, "Jet 30", 53), (13, "Jet 31", 54), (14, "Jet 32", 55), (15, "Jet 33", 56),
]
NEW_DEVICES = [
    {"name": "{} Temp".format(jet), "slave_id": slave, "vfd_model": MODEL,
     "component_instance_id": comp}
    for slave, jet, comp in METERS
]


def fail(message):
    print("NOT CHANGED: " + message)
    sys.exit(1)


def main():
    for name in ("gateway_service.py", "config.json"):
        if not os.path.exists(os.path.join(GATEWAY_DIR, name)):
            fail("{} not found in {}. Put this script in the gateway folder "
                 "(/home/pi/gateway) and run it from there.".format(name, GATEWAY_DIR))

    # 1. The gateway code must already know the new model.
    sys.path.insert(0, GATEWAY_DIR)
    import gateway_service
    if MODEL not in gateway_service.VFD_REGISTER_MAPS:
        fail("gateway_service.py in {} doesn't know {} yet. Copy the new "
             "gateway_service.py in first, then run this again.".format(GATEWAY_DIR, MODEL))

    with open(CONFIG, encoding="utf-8") as fh:
        config = json.load(fh)

    # 2. The bus and the temperature tag.
    buses = config.get("buses", [])
    bus = next((b for b in buses if b.get("port") == BUS_PORT), None)
    if bus is None:
        fail("there is no bus with port {} in config.json.".format(BUS_PORT))
    tag = config.get("tag_definition_ids", {}).get("temperature")
    if tag != TEMPERATURE_TAG_ID:
        fail('tag_definition_ids["temperature"] is {!r}, expected {}.'.format(tag, TEMPERATURE_TAG_ID))

    # 3. Already done? Anything in the way?
    devices = bus["devices"]
    present = sorted((d.get("slave_id"), d.get("component_instance_id"))
                     for d in devices if d.get("vfd_model") == MODEL)
    if present == [(slave, comp) for slave, _, comp in METERS]:
        print("All 12 temperature meters are already in config.json. Nothing to do.")
        return

    wanted_slaves = {slave for slave, _, _ in METERS}
    wanted_comps = {comp for _, _, comp in METERS}
    problems = []
    for d in devices:                               # slave ids only clash on the same bus
        if d.get("slave_id") in wanted_slaves:
            problems.append('slave {} on {} is already used by "{}"'.format(
                d.get("slave_id"), BUS_PORT, d.get("name")))
    for b in buses:                                 # component ids are global
        for d in b.get("devices", []):
            if d.get("component_instance_id") in wanted_comps:
                problems.append('component id {} is already used by "{}" on {}'.format(
                    d.get("component_instance_id"), d.get("name"), b.get("port")))
    if problems:
        fail("\n  " + "\n  ".join(problems))

    # 4. Back up, write atomically, check with the gateway's own loader.
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup = CONFIG + ".bak-" + stamp
    shutil.copy2(CONFIG, backup)
    devices.extend(NEW_DEVICES)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, CONFIG)
    try:
        gateway_service.load_config()
    except Exception as exc:
        shutil.copy2(backup, CONFIG)
        fail("the gateway's own config check rejected the new file ({}). "
             "The old config.json was put back.".format(exc))

    print("OK: added 12 temperature meters to {} (slaves 4-15 -> components 45-56).".format(BUS_PORT))
    print("Backup of the old file: " + backup)
    print("Next: sudo systemctl restart gateway && tail -f gateway.log")


if __name__ == "__main__":
    main()
