"""
Temperature & Pressure board endpoint (read-only).

GET /pressure/board

Everything the tile board needs that /machines/live does not carry:
  - which machines have a pressure/temperature sensor at all (so a sensor that
    has been silent for more than /machines/live's 10-minute window still gets
    a tile instead of silently vanishing),
  - each sensor's last-ever reading + timestamp (for "Last seen Xm ago"),
  - a 1-hour sparkline, the peak for the current shift, and the hysteresis
    alarm state / how long it has been above the limit.

/machines/live stays the source for the fresh numbers and the running/idle
state; this endpoint only adds what it cannot give. No existing schema or
endpoint is changed.

Sensors are resolved by tag key ('pressure', 'temperature') through
component_type_tag, never by hardcoded ids or a jet list, so any machine that
gets a pressure/temperature sensor later appears automatically.

Thresholds are imported from services/alert_scheduler so the board and the
Telegram alert can never drift apart. Shift boundaries reuse
routers.water._op_day_bounds_utc (09:00 / 21:00 IST anchors, naive UTC).
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from routers.water import _op_day_bounds_utc, IST_OFFSET
from schemas.pressure_board import (
    BoardReading,
    BoardSparkPoint,
    PressureBoardMachine,
    PressureBoardResponse,
)
from services.alert_scheduler import PRESSURE_ALERT_THRESHOLD, PRESSURE_CLEAR_THRESHOLD

router = APIRouter(prefix="/pressure", tags=["pressure-board"])

SPARKLINE_MINUTES = 60


def _current_shift(now_utc: datetime):
    """(name, start, end) of the shift containing now_utc (naive UTC)."""
    ist_now = now_utc + IST_OFFSET
    op_date = ist_now.date() if ist_now.hour >= 9 else ist_now.date() - timedelta(days=1)
    day_start, shift_split, day_end = _op_day_bounds_utc(op_date)
    if now_utc < shift_split:
        return "A", day_start, shift_split
    return "B", shift_split, day_end


def _last_reading(db: Session, company_id: int, cid: int, tag_id: int) -> BoardReading:
    # No time bound on purpose: this is the "last seen" fallback for sensors
    # that /machines/live's 10-minute window has already dropped. Single
    # indexed descending LIMIT 1 per sensor, same shape as water.py's carry-in.
    row = db.execute(text("""
        SELECT timestamp, value_num
        FROM telemetry_data
        WHERE component_instance_id = :cid
          AND tag_definition_id     = :tag_id
          AND company_id            = :company_id
        ORDER BY timestamp DESC
        LIMIT 1
    """), {"cid": cid, "tag_id": tag_id, "company_id": company_id}).fetchone()
    if row is None:
        return BoardReading(value=None, timestamp=None)
    return BoardReading(
        value=float(row.value_num) if row.value_num is not None else None,
        timestamp=row.timestamp,
    )


def _replay_alarm(buckets: list[tuple[datetime, float]]):
    """Replay the alert/clear hysteresis over (bucket_start, value) pairs in
    time order. Returns (active, since, truncated)."""
    active, since = False, None
    for ts, v in buckets:
        if not active and v > PRESSURE_ALERT_THRESHOLD:
            active, since = True, ts
        elif active and v <= PRESSURE_CLEAR_THRESHOLD:
            active, since = False, None
    truncated = bool(active and buckets and since == buckets[0][0])
    return active, since, truncated


@router.get("/board", response_model=PressureBoardResponse)
def get_pressure_board(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    company_id = current_user["company_id"]
    now_utc = datetime.utcnow()   # naive UTC — matches telemetry_data.timestamp
    shift, shift_start, shift_end = _current_shift(now_utc)

    sensors = db.execute(text("""
        SELECT DISTINCT
            ci.id         AS cid,
            ci.machine_id AS machine_id,
            m.name        AS machine_name,
            td.id         AS tag_id,
            td.key        AS tag_key
        FROM machine_component_instance ci
        JOIN machine m             ON m.id = ci.machine_id
        JOIN component_type_tag ctt
          ON ctt.component_type_id = ci.component_type_id
         AND ctt.company_id        = ci.company_id
        JOIN tag_definition td     ON td.id = ctt.tag_definition_id
        WHERE ci.company_id = :company_id
          AND td.company_id = :company_id
          AND td.key IN ('pressure', 'temperature')
        ORDER BY ci.machine_id, ci.id
    """), {"company_id": company_id}).mappings().fetchall()

    # One pressure + one temperature component per machine (lowest component
    # id wins if a machine ever has several).
    by_machine: dict[int, dict] = {}
    for s in sensors:
        entry = by_machine.setdefault(s["machine_id"], {"name": s["machine_name"]})
        entry.setdefault(s["tag_key"], (s["cid"], s["tag_id"]))

    # Bucketed pressure history for every pressure component in one query.
    # Window covers both the shift (for the peak) and the last hour (for the
    # sparkline); starting at the earlier of the two also lets the alarm replay
    # see how long an excursion has really been running.
    window_start = min(shift_start, now_utc - timedelta(minutes=SPARKLINE_MINUTES))
    pressure_cids = [v["pressure"][0] for v in by_machine.values() if "pressure" in v]
    history: dict[int, list[tuple[datetime, float, float]]] = {}
    if pressure_cids:
        pressure_tag_id = next(v["pressure"][1] for v in by_machine.values() if "pressure" in v)
        rows = db.execute(
            text("""
                SELECT component_instance_id AS cid,
                       time_bucket('1 minute', timestamp) AS b,
                       last(value_num, timestamp)         AS v,
                       max(value_num)                     AS mx
                FROM telemetry_data
                WHERE component_instance_id IN :cids
                  AND tag_definition_id = :tag_id
                  AND company_id        = :company_id
                  AND timestamp         > :window_start
                  AND timestamp        <= :now
                  AND value_num IS NOT NULL
                GROUP BY component_instance_id, b
                ORDER BY component_instance_id, b
            """).bindparams(bindparam("cids", expanding=True)),
            {"cids": pressure_cids, "tag_id": pressure_tag_id, "company_id": company_id,
             "window_start": window_start, "now": now_utc},
        ).mappings().fetchall()
        for r in rows:
            history.setdefault(r["cid"], []).append((r["b"], float(r["v"]), float(r["mx"])))

    spark_from = now_utc - timedelta(minutes=SPARKLINE_MINUTES)
    machines: list[PressureBoardMachine] = []
    for machine_id, info in by_machine.items():
        p_sensor, t_sensor = info.get("pressure"), info.get("temperature")

        pressure = _last_reading(db, company_id, *p_sensor) if p_sensor else None
        temperature = _last_reading(db, company_id, *t_sensor) if t_sensor else None

        buckets = history.get(p_sensor[0], []) if p_sensor else []
        active, since, truncated = _replay_alarm([(b, v) for b, v, _ in buckets])
        peaks = [mx for b, _, mx in buckets if b >= shift_start]

        machines.append(PressureBoardMachine(
            machine_id=machine_id,
            machine_name=info["name"],
            pressure=pressure,
            temperature=temperature,
            alarm_active=active,
            above_since=since,
            above_since_truncated=truncated,
            shift_peak=max(peaks) if peaks else None,
            sparkline=[BoardSparkPoint(t=b, v=v) for b, v, _ in buckets if b >= spark_from],
        ))

    return PressureBoardResponse(
        generated_at=now_utc,
        shift=shift,
        shift_start=shift_start,
        shift_end=shift_end,
        alert_threshold=PRESSURE_ALERT_THRESHOLD,
        clear_threshold=PRESSURE_CLEAR_THRESHOLD,
        machines=machines,
    )
