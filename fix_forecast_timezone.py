"""One-time backfill: convert forecast_log time arrays from local Pacific time to UTC.

Old forecasts were fetched with timezone='auto' so times are stored as local
Pacific time (e.g. "2026-02-28T15:00"). New code uses timezone='UTC', and the
solar chart appends 'Z' to timestamps. Without this fix, old solar forecast
data appears 7–8 hours shifted in the browser.

Run BEFORE restarting services (so no new UTC rows exist yet):
    python3 fix_forecast_timezone.py

Dry-run (shows a sample of changes without writing):
    python3 fix_forecast_timezone.py --dry-run
"""

import json
import shutil
import sys
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = "greenhouse.db"
DRY_RUN = "--dry-run" in sys.argv


# ---------------------------------------------------------------------------
# Pacific timezone offset: hours to ADD to local Pacific time to get UTC.
# PST = UTC-8 (8h to add), PDT = UTC-7 (7h to add).
# ---------------------------------------------------------------------------

def _pacific_offset(utc_dt):
    """Return hours to add to Pacific local time to reach UTC."""
    year = utc_dt.year
    # Second Sunday of March at 2am PST = 10:00 UTC → DST starts
    march1 = datetime(year, 3, 1)
    d, sundays = march1, 0
    while sundays < 2:
        if d.weekday() == 6:
            sundays += 1
        if sundays < 2:
            d += timedelta(days=1)
    dst_on = datetime(year, d.month, d.day, 10, 0)

    # First Sunday of November at 2am PDT = 09:00 UTC → DST ends
    nov1 = datetime(year, 11, 1)
    d = nov1
    while d.weekday() != 6:
        d += timedelta(days=1)
    dst_off = datetime(year, d.month, d.day, 9, 0)

    return 7 if dst_on <= utc_dt < dst_off else 8


def _shift_times(times, hours):
    """Add `hours` to each time string in the list, return new list."""
    out = []
    for t in times:
        try:
            out.append((datetime.fromisoformat(t) + timedelta(hours=hours))
                       .strftime("%Y-%m-%dT%H:00"))
        except Exception:
            out.append(t)
    return out


def _convert_fc(fc_dict, hours):
    """Return a copy of fc_dict with the 'time' array shifted by hours."""
    fc = dict(fc_dict)
    if "time" in fc:
        fc["time"] = _shift_times(fc["time"], hours)
    return fc


def main():
    db_path = Path(DB_PATH)
    backup_path = db_path.with_suffix(
        f".backup_tz_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    )
    if not DRY_RUN:
        shutil.copy2(db_path, backup_path)
        print(f"Backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = [dict(r) for r in conn.execute(
        "SELECT id, timestamp, raw_forecast, corrected_forecast FROM forecast_log ORDER BY rowid ASC"
    ).fetchall()]
    print(f"Total forecast_log rows: {len(rows)}")

    updated = skipped = errors = 0
    for row in rows:
        try:
            cf = json.loads(row["corrected_forecast"])
            times = cf.get("time", [])
            if not times:
                skipped += 1
                continue

            # Detect if already UTC: local-format rows always start at T00:00 (local midnight).
            # After conversion they start at T07:00 or T08:00 (UTC equivalent).
            first_hour = int(times[0][11:13])
            if first_hour != 0:
                # Already converted (or unexpected format) — skip.
                skipped += 1
                continue

            # Determine Pacific offset from the row's UTC log timestamp.
            log_ts = datetime.fromisoformat(
                row["timestamp"].replace("+00:00", "").replace("Z", "")
            )
            offset = _pacific_offset(log_ts)

            new_cf = _convert_fc(cf, offset)

            new_rf_json = row["raw_forecast"]
            if row["raw_forecast"]:
                rf = json.loads(row["raw_forecast"])
                new_rf_json = json.dumps(_convert_fc(rf, offset))

            if DRY_RUN:
                if updated < 3:
                    print(f"  row {row['id']} @ {row['timestamp'][:19]}: "
                          f"offset=+{offset}h, "
                          f"times[0] {times[0]} → {new_cf['time'][0]}")
            else:
                conn.execute(
                    "UPDATE forecast_log SET corrected_forecast=?, raw_forecast=? WHERE id=?",
                    (json.dumps(new_cf), new_rf_json, row["id"]),
                )
            updated += 1

        except Exception as e:
            print(f"  row {row['id']}: error — {e}")
            errors += 1

    if not DRY_RUN:
        conn.commit()
    conn.close()

    action = "Would update" if DRY_RUN else "Updated"
    print(f"\n{action} {updated} rows, skipped {skipped} (already UTC or empty), {errors} errors.")
    if DRY_RUN:
        print("Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()
