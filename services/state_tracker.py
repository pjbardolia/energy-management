"""
Machine state-change tracker.

Runs a background APScheduler job every 60 seconds. Compares each machine's
most recent frequency reading (tag_definition_id=6 — the exact same
RUNNING/STOPPED threshold already used by the Fleet tile's getMachineState()
and routers/runtime.py::FREQUENCY_TAG_ID: frequency > 0 = running) against the
currently-open machine_state_event row for that machine, and writes a new
row only when the state actually changed.

Design rationale (Option B): a dedicated event table instead of
reconstructing state from raw telemetry on every page view — querying a
handful of rows per machine per day is far lighter on server memory/CPU than
re-scanning tens of thousands of raw telemetry rows, which matters given this
droplet has previously hit real OOM crashes on 961MB RAM. A machine running
steadily all day produces exactly one open row, not one row per poll.

Mirrors the existing services/alert_scheduler.py pattern (own
BackgroundScheduler instance, SessionLocal per job run, broad except so one
bad cycle never kills the scheduler) rather than inventing a new background
job convention.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text

from database import SessionLocal

log = logging.getLogger(__name__)

# Hard contract — matches routers/runtime.py::FREQUENCY_TAG_ID and the
# threshold the Fleet tile's getMachineState() uses (frequency > 0 = running).
FREQUENCY_TAG_ID = 6
COMPANY_ID       = 1   # SSPPL — extend to multi-tenant later (matches alert_scheduler.py)

# Ignore machines with no reading in the last N minutes — a gateway outage
# or a machine that simply has no sensor should never fabricate a false
# "stopped" transition. The open interval just keeps reflecting the last
# known state until a fresh reading arrives.
FRESH_WINDOW_MIN = 5


def check_state_transitions() -> None:
    """
    For every machine with a fresh frequency reading, compare its derived
    state (running if latest_freq > 0, else stopped) against the currently
    open machine_state_event row and write a transition if they differ.
    """
    db = SessionLocal()
    try:
        # last(value_num, timestamp) is a TimescaleDB aggregate — same pattern
        # already used by services/alert_scheduler.py::check_overcurrent().
        sql = text("""
            SELECT
                m.id                              AS machine_id,
                last(td.value_num, td.timestamp)  AS latest_freq,
                max(td.timestamp)                 AS latest_ts
            FROM telemetry_data td
            JOIN machine_component_instance ci ON ci.id = td.component_instance_id
            JOIN machine m                     ON m.id = ci.machine_id
            WHERE td.tag_definition_id = :freq_tag
              AND td.company_id        = :company_id
              AND td.timestamp        > NOW() - INTERVAL '{window} minutes'
            GROUP BY m.id
        """.format(window=FRESH_WINDOW_MIN))

        rows = db.execute(sql, {
            "freq_tag":   FREQUENCY_TAG_ID,
            "company_id": COMPANY_ID,
        }).mappings().fetchall()

        for row in rows:
            machine_id  = row["machine_id"]
            latest_freq = float(row["latest_freq"]) if row["latest_freq"] is not None else 0.0
            latest_ts   = row["latest_ts"]
            new_state   = "running" if latest_freq > 0 else "stopped"

            open_row = db.execute(text("""
                SELECT id, state FROM machine_state_event
                WHERE machine_id = :machine_id AND company_id = :company_id AND ended_at IS NULL
                ORDER BY started_at DESC LIMIT 1
            """), {"machine_id": machine_id, "company_id": COMPANY_ID}).mappings().first()

            if open_row is None:
                # First observation ever for this machine — open the initial
                # interval. Self-heals across the fleet within a few minutes
                # of first deploy as each machine gets its next fresh reading.
                db.execute(text("""
                    INSERT INTO machine_state_event (machine_id, company_id, state, started_at)
                    VALUES (:machine_id, :company_id, :state, :started_at)
                """), {
                    "machine_id": machine_id, "company_id": COMPANY_ID,
                    "state": new_state, "started_at": latest_ts,
                })
                db.commit()
                log.info("State tracker: opened initial %s interval for machine %d",
                          new_state, machine_id)
                continue

            if open_row["state"] == new_state:
                # No change — nothing to write, keeping the table small.
                continue

            # Transition: close the old interval, open a new one at the same instant.
            db.execute(text("""
                UPDATE machine_state_event SET ended_at = :ended_at WHERE id = :id
            """), {"ended_at": latest_ts, "id": open_row["id"]})
            db.execute(text("""
                INSERT INTO machine_state_event (machine_id, company_id, state, started_at)
                VALUES (:machine_id, :company_id, :state, :started_at)
            """), {
                "machine_id": machine_id, "company_id": COMPANY_ID,
                "state": new_state, "started_at": latest_ts,
            })
            db.commit()
            log.info("State tracker: machine %d transitioned %s -> %s at %s",
                      machine_id, open_row["state"], new_state, latest_ts)

    except Exception as exc:
        db.rollback()
        log.error("Error in check_state_transitions: %s", exc)
    finally:
        db.close()


def start_state_tracker() -> BackgroundScheduler:
    """
    Start the state-transition background scheduler.

    Called once at FastAPI startup via the lifespan context manager, as a
    separate BackgroundScheduler instance from the alert scheduler — kept
    independent so each can be started/shut down/reasoned about on its own,
    same as how alert_scheduler.py owns its own scheduler.
    """
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        check_state_transitions,
        trigger="interval",
        seconds=60,
        id="machine_state_transition_check",
        name="Machine state transition tracker",
        misfire_grace_time=30,
    )
    scheduler.start()
    log.info("Machine state tracker started — checking transitions every 60s")
    return scheduler
