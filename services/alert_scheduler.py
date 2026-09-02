"""
Alert scheduler for mevion platform.

Runs a background APScheduler job every 60 seconds checking:
1. Gateway offline (>20 minutes without heartbeat) — alert once, recover once
2. Motor overcurrent (current > OVERCURRENT_THRESHOLD_A) — alert once per
   machine, recover once per machine when current drops back below threshold
3. Machine stop/start — alert once per machine when it stops, recover once
   when it starts running again, with the stopped duration

Alert state is kept in memory — resets on server restart, which is acceptable
since the scheduler will re-detect any active conditions within 60 seconds.

Machine stop/start alerts do NOT recompute running/stopped from raw telemetry
— they read the same machine_state_event table that already powers the
Uptime tab's Gantt Timeline, Heatmap Calendar, and Machine Log (written by
services/state_tracker.py's frequency>0 threshold check, its own separate
60s job). Reading the same stored fact the dashboard reads guarantees this
alert can never disagree with what the dashboard shows for the same moment.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text

from database import SessionLocal
from models.gateway_heartbeat import GatewayHeartbeat
from services.telegram import send_alert

log = logging.getLogger(__name__)

# --- Configuration ---
GATEWAY_OFFLINE_THRESHOLD_MINUTES = 20
OVERCURRENT_THRESHOLD_A           = 13.0  # Amperes — alert when any machine exceeds this
COMPANY_ID                        = 1     # SSPPL — extend to multi-tenant later

# Separate Telegram destination for machine stop/start alerts ("Machine
# ON/OFF" group) — same bot token as everything else (services/telegram.py),
# just a different chat_id. Deliberately does NOT fall back to the default
# TELEGRAM_CHAT_ID if unset — see check_machine_state_alerts(), which checks
# this is truthy before calling send_alert at all, so a misconfiguration
# shows up as a clear skipped-send warning rather than silently mixing
# machine state alerts into the overcurrent chat.
MACHINE_STATE_CHAT_ID = os.environ.get("TELEGRAM_MACHINE_STATE_CHAT_ID", "")

# --- In-memory alert state ---
# Prevents sending duplicate alerts every 60 seconds.
# Resets on server restart; scheduler re-detects within one cycle.
_gateway_alert_sent    = False
_overcurrent_machines: dict[str, bool] = {}  # machine_name -> True if alert active

# machine_id -> (last_known_state, started_at of that state's interval).
# Tuple (not a plain bool like _overcurrent_machines) because the recovery
# message needs to know when the stopped interval began, to report how long
# the machine was down.
_machine_run_state: dict[int, tuple[str, datetime]] = {}


def check_gateway_offline() -> None:
    """
    Check if gateway heartbeat is older than GATEWAY_OFFLINE_THRESHOLD_MINUTES.
    Send alert once when it goes offline, send recovery once when it comes back.
    """
    global _gateway_alert_sent

    db = SessionLocal()
    try:
        row = db.query(GatewayHeartbeat).filter(
            GatewayHeartbeat.company_id == COMPANY_ID
        ).first()

        if not row or not row.last_seen:
            # No heartbeat ever received — don't alert (system may be starting up)
            return

        now       = datetime.now(timezone.utc)
        # Normalise to UTC in case the driver returns a naive datetime
        last_seen = (
            row.last_seen.replace(tzinfo=timezone.utc)
            if row.last_seen.tzinfo is None
            else row.last_seen
        )
        minutes_ago = (now - last_seen).total_seconds() / 60
        is_offline  = minutes_ago > GATEWAY_OFFLINE_THRESHOLD_MINUTES

        if is_offline and not _gateway_alert_sent:
            # Gateway just went offline — send alert
            last_seen_ist = last_seen + timedelta(hours=5, minutes=30)
            msg = (
                f"🔴 <b>Gateway Offline — SSPPL</b>\n\n"
                f"No data received for {int(minutes_ago)} minutes.\n"
                f"Last seen: {last_seen_ist.strftime('%d %b, %I:%M %p')} IST\n\n"
                f"Check factory internet or Pi at 192.168.0.200"
            )
            send_alert(msg)
            _gateway_alert_sent = True
            log.warning("Gateway offline alert sent (last seen %.0f min ago)", minutes_ago)

        elif not is_offline and _gateway_alert_sent:
            # Gateway just came back online — send recovery
            offline_duration = timedelta(minutes=minutes_ago)
            hours   = int(offline_duration.total_seconds() // 3600)
            minutes = int((offline_duration.total_seconds() % 3600) // 60)
            duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

            msg = (
                f"✅ <b>Gateway Recovered — SSPPL</b>\n\n"
                f"Back online after {duration_str} offline.\n"
                f"All {row.machines_polled or 14} machines resuming normal monitoring."
            )
            send_alert(msg)
            _gateway_alert_sent = False
            log.info("Gateway recovery alert sent")

    except Exception as exc:
        log.error("Error in check_gateway_offline: %s", exc)
    finally:
        db.close()


def check_overcurrent() -> None:
    """
    Check latest current readings for all machines.
    Alert when any machine exceeds OVERCURRENT_THRESHOLD_A.
    Recover when it drops back below threshold.

    Reads from telemetry_data using the most recent reading per
    component_instance. tag_definition_id=3 is 'current' (Amperes) —
    this is a hard DB contract that must not change.
    The TimescaleDB last() aggregate returns the value at the latest timestamp.
    """
    global _overcurrent_machines

    db = SessionLocal()
    try:
        # last(value_num, timestamp) is a TimescaleDB aggregate: returns value_num
        # from the row with the latest timestamp within each GROUP.
        # Only considers readings from the last 5 minutes — avoids stale alerts
        # when the gateway has been offline for a while.
        sql = text("""
            SELECT
                m.name                         AS machine_name,
                ci.id                          AS component_id,
                last(td.value_num, td.timestamp) AS latest_current,
                max(td.timestamp)              AS last_updated
            FROM telemetry_data td
            JOIN machine_component_instance ci ON ci.id = td.component_instance_id
            JOIN machine m ON m.id = ci.machine_id
            WHERE td.tag_definition_id = 3
              AND td.company_id = :company_id
              AND td.timestamp > NOW() - INTERVAL '5 minutes'
            GROUP BY m.name, ci.id
            ORDER BY m.name
        """)

        result = db.execute(sql, {"company_id": COMPANY_ID})
        rows = result.mappings().fetchall()

        for row in rows:
            machine_name   = row["machine_name"]
            latest_current = float(row["latest_current"]) if row["latest_current"] is not None else 0.0
            is_over        = latest_current > OVERCURRENT_THRESHOLD_A
            was_over       = _overcurrent_machines.get(machine_name, False)

            if is_over and not was_over:
                # Machine just went overcurrent — send alert
                msg = (
                    f"⚠️ <b>High Current — {machine_name} (SSPPL)</b>\n\n"
                    f"Current: <b>{latest_current:.1f} A</b> "
                    f"(threshold: {OVERCURRENT_THRESHOLD_A:.0f} A)\n\n"
                    f"Check motor load, nozzle blockage, and fabric condition."
                )
                send_alert(msg)
                _overcurrent_machines[machine_name] = True
                log.warning("Overcurrent alert: %s at %.1f A", machine_name, latest_current)

            elif not is_over and was_over:
                # Machine recovered — send recovery
                msg = (
                    f"✅ <b>{machine_name} Current Normal — SSPPL</b>\n\n"
                    f"Current back to {latest_current:.1f} A "
                    f"(was above {OVERCURRENT_THRESHOLD_A:.0f} A threshold)."
                )
                send_alert(msg)
                _overcurrent_machines[machine_name] = False
                log.info("Overcurrent recovery: %s at %.1f A", machine_name, latest_current)

    except Exception as exc:
        log.error("Error in check_overcurrent: %s", exc)
    finally:
        db.close()


def check_machine_state_alerts() -> None:
    """
    Alert on machine stop/start transitions.

    Reads ONLY the currently-open interval per machine from
    machine_state_event (WHERE ended_at IS NULL) — the exact same stored
    fact the Uptime tab's Gantt Timeline, Heatmap Calendar, and Machine Log
    already display. Does not recompute running/stopped from telemetry;
    that determination happens once, in services/state_tracker.py's own
    60s job, and is never duplicated here.

    First observation of a machine (cache empty — e.g. right after a service
    restart) seeds the cache silently, without alerting. Without this, every
    restart would fire a stop-alert for every machine that happened to
    already be stopped at that moment.
    """
    global _machine_run_state

    db = SessionLocal()
    try:
        sql = text("""
            SELECT
                m.id         AS machine_id,
                m.name       AS machine_name,
                e.state,
                e.started_at
            FROM machine_state_event e
            JOIN machine m ON m.id = e.machine_id
            WHERE e.company_id = :company_id
              AND e.ended_at IS NULL
        """)

        rows = db.execute(sql, {"company_id": COMPANY_ID}).mappings().fetchall()

        for row in rows:
            machine_id   = row["machine_id"]
            machine_name = row["machine_name"]
            state        = row["state"]
            started_at   = row["started_at"]
            # Normalise to UTC in case the driver returns a naive datetime
            # (same guard already used in check_gateway_offline).
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)

            last = _machine_run_state.get(machine_id)

            if last is None:
                # First observation for this machine — nothing to compare
                # against. Seed the cache, do not alert.
                _machine_run_state[machine_id] = (state, started_at)
                continue

            last_state, last_started_at = last

            if state == last_state:
                # No change — this is the anti-spam guarantee: one alert per
                # transition, silence while the state persists.
                continue

            now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            started_at_ist = started_at + timedelta(hours=5, minutes=30)

            if state == "stopped":
                msg = (
                    f"🔴 <b>{machine_name} STOPPED</b> — SSPPL\n\n"
                    f"Stopped at {started_at_ist.strftime('%d %b, %I:%M %p')} IST"
                )
                if MACHINE_STATE_CHAT_ID:
                    send_alert(msg, chat_id=MACHINE_STATE_CHAT_ID)
                    log.warning("Machine stop alert: %s at %s", machine_name, started_at_ist)
                else:
                    log.warning(
                        "Machine state alert chat not configured "
                        "(TELEGRAM_MACHINE_STATE_CHAT_ID unset) — skipping send: %s", msg,
                    )

            elif state == "running" and last_state == "stopped":
                # last_started_at is when the STOPPED interval began (the
                # value cached the last time we saw this machine as stopped),
                # so duration = now - that instant.
                stopped_duration = datetime.now(timezone.utc) - last_started_at
                hours   = int(stopped_duration.total_seconds() // 3600)
                minutes = int((stopped_duration.total_seconds() % 3600) // 60)
                duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

                msg = (
                    f"🟢 <b>{machine_name} RUNNING again</b> — SSPPL\n\n"
                    f"Back up at {now_ist.strftime('%d %b, %I:%M %p')} IST "
                    f"(stopped for {duration_str})"
                )
                if MACHINE_STATE_CHAT_ID:
                    send_alert(msg, chat_id=MACHINE_STATE_CHAT_ID)
                    log.info("Machine recovery alert: %s after %s stopped", machine_name, duration_str)
                else:
                    log.warning(
                        "Machine state alert chat not configured "
                        "(TELEGRAM_MACHINE_STATE_CHAT_ID unset) — skipping send: %s", msg,
                    )

            _machine_run_state[machine_id] = (state, started_at)

    except Exception as exc:
        log.error("Error in check_machine_state_alerts: %s", exc)
    finally:
        db.close()


def start_alert_scheduler() -> BackgroundScheduler:
    """
    Start the APScheduler background scheduler.
    Called once at FastAPI startup via the lifespan context manager.
    Returns the scheduler instance so the lifespan can shut it down cleanly.
    """
    scheduler = BackgroundScheduler(timezone="UTC")

    # Gateway offline check: every 60 seconds
    scheduler.add_job(
        check_gateway_offline,
        trigger="interval",
        seconds=60,
        id="gateway_offline_check",
        name="Gateway offline checker",
        misfire_grace_time=30,
    )

    # Overcurrent check: every 60 seconds, offset 30 s so the two jobs
    # don't both hit the DB simultaneously on startup
    scheduler.add_job(
        check_overcurrent,
        trigger="interval",
        seconds=60,
        id="overcurrent_check",
        name="Motor overcurrent checker",
        misfire_grace_time=30,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
    )

    # Machine stop/start check: every 60 seconds, offset 15 s from the other
    # two jobs so all three don't hit the DB in the same instant
    scheduler.add_job(
        check_machine_state_alerts,
        trigger="interval",
        seconds=60,
        id="machine_state_alert_check",
        name="Machine stop/start alert checker",
        misfire_grace_time=30,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
    )

    scheduler.start()
    log.info("Alert scheduler started — gateway, overcurrent, and machine state checks every 60s")
    return scheduler
