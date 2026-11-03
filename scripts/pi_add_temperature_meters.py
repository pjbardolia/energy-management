#!/usr/bin/env python3
"""
pi_add_temperature_meters.py — adds the 12 iTherm FX-438 temperature meters
(Jets 21-26 and 28-33) to the gateway's config.json on the Pi. Safe to re-run.

Run it ON THE PI, from the gateway folder, without sudo, AFTER the new
gateway_service.py (the one that knows ITHERM_FX438_TENTHS) is in place and
BEFORE restarting the gateway:

    cd /home/pi/gateway
    python3 pi_add_temperature_meters.py --skip 23     # leave Jet 23 out

--skip takes jet numbers (e.g. --skip 23 or --skip 23 31) for meters that are
not on the bus: a missing meter costs the gateway up to ~4 s of timeouts on
every poll cycle. Meters already in config.json are left as they are, so once
a skipped meter is back, run it again without --skip to add just that one.

What it does, in order:
  1. Refuses unless gateway_service.py in this folder knows ITHERM_FX438_TENTHS.
     A config naming a model the code doesn't know stops the gateway from
     starting at all (systemd retries every 10 s, with no data from any bus).
  2. Refuses unless config.json has the /dev/ttyUSB0 bus and
     tag_definition_ids["temperature"] is 8.
  3. For each meter to add: skips it if the exact entry is already there;
     refuses if its slave id (on /dev/ttyUSB0) or its component id (on any
     bus) is already used by something else.
  4. Copies config.json to config.json.bak-<date_time>, writes the new file,
     then checks it with gateway_service.load_config() — the same check the
     gateway runs at startup. If that check fails, it puts the backup back.

It never removes entries and never restarts the gateway: you restart after it
prints OK.
"""

import argparse
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
def fail(message):
    print("NOT CHANGED: " + message)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Add the FX-438 temperature meters to config.json.")
    parser.add_argument("--skip", nargs="+", type=int, default=[], metavar="JET",
                        help="jet numbers whose meter is not on the bus, e.g. --skip 23")
    args = parser.parse_args()
    known_jets = {int(jet.split()[1]) for _, jet, _ in METERS}
    unknown = sorted(set(args.skip) - known_jets)
    if unknown:
        fail("--skip {}: not one of the 12 new meters (Jets {}).".format(
            " ".join(map(str, unknown)), ", ".join(str(j) for j in sorted(known_jets))))
    wanted = [m for m in METERS if int(m[1].split()[1]) not in args.skip]

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

    # 3. What's already there, and is anything in the way?
    devices = bus["devices"]
    existing = {(d.get("slave_id"), d.get("component_instance_id"), d.get("vfd_model")) for d in devices}
    to_add, already, problems = [], [], []
    for slave, jet, comp in wanted:
        if (slave, comp, MODEL) in existing:
            already.append(jet)
            continue
        for d in devices:                           # slave ids only clash on the same bus
            if d.get("slave_id") == slave:
                problems.append('{}: slave {} on {} is already used by "{}"'.format(
                    jet, slave, BUS_PORT, d.get("name")))
        for b in buses:                             # component ids are global
            for d in b.get("devices", []):
                if d.get("component_instance_id") == comp:
                    problems.append('{}: component id {} is already used by "{}" on {}'.format(
                        jet, comp, d.get("name"), b.get("port")))
        to_add.append({"name": "{} Temp".format(jet), "slave_id": slave,
                       "vfd_model": MODEL, "component_instance_id": comp})
    if problems:
        fail("\n  " + "\n  ".join(problems))
    skipped = ", ".join("Jet {}".format(j) for j in sorted(args.skip))
    if not to_add:
        print("Nothing to do: {} already in config.json{}.".format(
            ", ".join(already) or "no meters", "; skipped " + skipped if skipped else ""))
        return

    # 4. Back up, write atomically, check with the gateway's own loader.
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup = CONFIG + ".bak-" + stamp
    shutil.copy2(CONFIG, backup)
    devices.extend(to_add)
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

    print("OK: added {} temperature meter(s) to {}:".format(len(to_add), BUS_PORT))
    for d in to_add:
        print("  {:12} slave {:>2} -> component {}".format(d["name"], d["slave_id"], d["component_instance_id"]))
    if already:
        print("Already there, unchanged: " + ", ".join(already))
    if skipped:
        print("Skipped (not on the bus): " + skipped)
    print("Backup of the old file: " + backup)
    print("Next: sudo systemctl restart gateway && tail -f gateway.log")


if __name__ == "__main__":
    main()
