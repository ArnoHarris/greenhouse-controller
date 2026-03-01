"""One-time backfill: correct shades_east, shades_west, fan_on in sensor_log
for the past 24 hours using the overrides table as the source of truth.

The old data-collection code logged actuator fields at their GreenhouseState
defaults (shades open, fan off) regardless of manual dashboard commands. This
script reconstructs the true commanded state at each timestamp by checking
which overrides were active (created_at <= ts < expires_at, not yet cancelled).

Run once on the Pi from the project directory:
    python3 fix_historical_actuators.py

Dry-run (shows changes without writing):
    python3 fix_historical_actuators.py --dry-run
"""

import json
import shutil
import sys
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = "greenhouse.db"
DRY_RUN = "--dry-run" in sys.argv


def override_active_at(ov, ts):
    """Return True if this override record was active at timestamp string ts."""
    return (
        ov["created_at"] <= ts
        and ov["expires_at"] > ts
        and (ov["cancelled_at"] is None or ov["cancelled_at"] > ts)
    )


def main():
    # Backup before touching anything
    db_path = Path(DB_PATH)
    backup_path = db_path.with_suffix(
        f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    )
    if not DRY_RUN:
        shutil.copy2(db_path, backup_path)
        print(f"Backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    now    = datetime.now(timezone.utc).isoformat()

    # Load all override records that could overlap with the past 24 hours.
    # (An override that expired before the cutoff can't affect any row we care about.)
    overrides = [
        dict(r) for r in conn.execute(
            """SELECT actuator, command, created_at, expires_at, cancelled_at
               FROM overrides
               WHERE datetime(expires_at) > datetime(?)
                 AND datetime(created_at) < datetime(?)
               ORDER BY created_at ASC""",
            (cutoff, now),
        ).fetchall()
    ]
    print(f"Relevant override records: {len(overrides)}")
    for ov in overrides:
        print(f"  {ov['actuator']:12s}  cmd={ov['command']:30s}  "
              f"from={ov['created_at'][:19]}  "
              f"to={ov['expires_at'][:19]}  "
              f"cancelled={ov['cancelled_at'][:19] if ov['cancelled_at'] else 'no'}")

    # Load sensor_log rows from the past 24 hours.
    rows = [
        dict(r) for r in conn.execute(
            """SELECT id, timestamp, shades_east, shades_west, fan_on
               FROM sensor_log
               WHERE datetime(timestamp) >= datetime(?)
               ORDER BY rowid ASC""",
            (cutoff,),
        ).fetchall()
    ]
    print(f"\nSensor_log rows in window: {len(rows)}")

    updated = 0
    for row in rows:
        ts = row["timestamp"]

        # For each actuator, find the most-recently-created override active at ts.
        active = {}
        for ov in overrides:
            if override_active_at(ov, ts):
                act = ov["actuator"]
                if act not in active or ov["created_at"] > active[act]["created_at"]:
                    active[act] = ov

        new_shades_east = row["shades_east"]
        new_shades_west = row["shades_west"]
        new_fan_on      = row["fan_on"]

        if "shades_east" in active:
            cmd = json.loads(active["shades_east"]["command"])
            new_shades_east = cmd.get("position", row["shades_east"])

        if "shades_west" in active:
            cmd = json.loads(active["shades_west"]["command"])
            new_shades_west = cmd.get("position", row["shades_west"])

        if "fan" in active:
            cmd = json.loads(active["fan"]["command"])
            new_fan_on = 1 if cmd.get("on", False) else 0

        changed = (
            new_shades_east != row["shades_east"]
            or new_shades_west != row["shades_west"]
            or new_fan_on != row["fan_on"]
        )

        if changed:
            print(f"  row {row['id']} @ {ts[:19]}: "
                  f"shades_east {row['shades_east']}→{new_shades_east}  "
                  f"shades_west {row['shades_west']}→{new_shades_west}  "
                  f"fan_on {row['fan_on']}→{new_fan_on}")
            if not DRY_RUN:
                conn.execute(
                    """UPDATE sensor_log
                       SET shades_east = ?, shades_west = ?, fan_on = ?
                       WHERE id = ?""",
                    (new_shades_east, new_shades_west, new_fan_on, row["id"]),
                )
            updated += 1

    if not DRY_RUN:
        conn.commit()
    conn.close()

    action = "Would update" if DRY_RUN else "Updated"
    print(f"\n{action} {updated} of {len(rows)} rows.")
    if DRY_RUN:
        print("Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()
