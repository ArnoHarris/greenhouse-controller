"""Fit thermal model parameters to observed sensor data.

Reads indoor/outdoor temps and solar irradiance from greenhouse.db, then uses
scipy.optimize to find model parameters that minimize prediction error.

Uses actual observed outdoor conditions (not forecast) as model inputs, so the
fit reflects pure thermal parameter error rather than forecast bias.

Usage:
    python fit_model.py                    # fit on last 7 days
    python fit_model.py --days 14          # fit on last 14 days
    python fit_model.py --days 7 --quiet   # suppress per-iteration output

The script prints suggested config.py values. It does NOT write them
automatically — review the results and update config.py manually.

Calibration tips:
  - Prefer windows with no shading and no fan overrides (cleaner physics)
  - Run multiple windows and compare; parameters should be stable
  - A positive mean_bias means model runs hot; negative means it runs cold
    (current complaint: model too cold → expect tau or UA changes)
"""

import argparse
import sqlite3
import os
import sys
from datetime import datetime, timezone, timedelta

import numpy as np

try:
    from scipy.optimize import minimize
except ImportError:
    print("ERROR: scipy not installed. Run: pip install scipy")
    sys.exit(1)

import config

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.DB_PATH)
G = config.GREENHOUSE

# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def _f_to_c(f): return (f - 32) * 5 / 9
def _c_to_f(c): return c * 9 / 5 + 32


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_sensor_data(days_back=7):
    """Return list of dicts from sensor_log, sorted by timestamp.

    Filters out rows missing any of the four key fields.
    Uses shades/fan state to flag rows (not excluded — we model them properly).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT timestamp, indoor_temp_f, outdoor_temp_f,
                  solar_irradiance_wm2, shades_east, shades_west, fan_on
           FROM sensor_log
           WHERE datetime(timestamp) >= ?
             AND indoor_temp_f IS NOT NULL
             AND outdoor_temp_f IS NOT NULL
             AND solar_irradiance_wm2 IS NOT NULL
           ORDER BY rowid ASC""",
        (cutoff,),
    ).fetchall()
    conn.close()
    print(f"Loaded {len(rows)} sensor rows (last {days_back} days).")
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Forward simulation using actual observed outdoor conditions
# ---------------------------------------------------------------------------

def simulate(rows, params):
    """Run the 2-node thermal model over the sensor data rows.

    Uses actual outdoor_temp and solar_irradiance from each row as model
    inputs. Integration step = time delta between consecutive rows
    (typically ~300 s / 5 minutes).

    Returns array of predicted indoor temps (°F), aligned to rows[1:].
    (Row 0 provides initial conditions; prediction starts at row 1.)
    """
    tau        = params["tau"]
    U_env      = params["U_env"]
    C_mass     = params["C_mass"]
    f_mass     = params["f_mass"]
    U_ground   = params["U_ground"]

    C_air    = G["air_heat_capacity_J_per_K"]
    UA_north = U_env * G["north_wall_area_m2"] * G["north_wall_U_factor"]
    UA_env   = U_env * G["envelope_area_m2"] + UA_north

    A_roof_east = config.ROOF_EAST_AREA_M2
    A_roof_west = config.ROOF_WEST_AREA_M2
    A_floor     = G["floor_area_m2"]
    roof_fraction = (A_roof_east + A_roof_west) / (A_roof_east + A_roof_west + A_floor)
    east_share    = A_roof_east / (A_roof_east + A_roof_west)
    west_share    = 1.0 - east_share

    rho_cp   = 1.2 * 1006  # J/m³/K
    fan_flow = G["fan_flow_m3_per_s"]

    T_air  = _f_to_c(rows[0]["indoor_temp_f"])
    T_mass = T_air  # no separate mass sensor; assume in equilibrium at start

    predicted = []

    for i in range(1, len(rows)):
        # Time delta for this integration step
        try:
            t0 = datetime.fromisoformat(rows[i - 1]["timestamp"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(rows[i]["timestamp"].replace("Z", "+00:00"))
            dt = (t1 - t0).total_seconds()
        except Exception:
            dt = 300.0  # default 5 minutes if parse fails
        if dt <= 0 or dt > 1800:
            dt = 300.0  # skip gaps > 30 min

        T_out  = _f_to_c(rows[i]["outdoor_temp_f"])
        I_sol  = max(0, rows[i]["solar_irradiance_wm2"] or 0)

        shade_east = 1.0 if rows[i].get("shades_east") == "closed" else 0.0
        shade_west = 1.0 if rows[i].get("shades_west") == "closed" else 0.0
        fan_on     = bool(rows[i].get("fan_on"))

        # Solar gain
        roof_solar = I_sol * tau * A_floor * roof_fraction * (
            east_share * (1 - shade_east) + west_share * (1 - shade_west)
        )
        wall_solar = I_sol * tau * A_floor * (1 - roof_fraction)
        Q_solar    = roof_solar + wall_solar

        Q_solar_air  = Q_solar * (1 - f_mass)
        Q_solar_mass = Q_solar * f_mass

        Q_envelope = UA_env   * (T_air - T_out)
        Q_vent     = rho_cp * (fan_flow if fan_on else 0.0) * (T_air - T_out)
        Q_ground   = U_ground * (T_air - T_mass)

        dT_air  = (Q_solar_air - Q_envelope - Q_vent - Q_ground) / C_air * dt
        dT_mass = (Q_solar_mass + U_ground * (T_air - T_mass)) / C_mass * dt

        T_air  += dT_air
        T_mass += dT_mass

        predicted.append(_c_to_f(T_air))

    return np.array(predicted)


# ---------------------------------------------------------------------------
# Objective function and fitting
# ---------------------------------------------------------------------------

# Parameter order: [tau, U_env, log10(C_mass), f_mass, U_ground]
BOUNDS = [
    (0.50, 0.95),    # tau: cover transmittance
    (2.0,  8.0),     # U_env: W/m²K
    (6.0,  7.5),     # log10(C_mass): 1e6 to 3e7 J/K
    (0.10, 0.80),    # f_mass: fraction solar → mass
    (30.0, 600.0),   # U_ground: W/K
]

PARAM_NAMES = ["tau", "U_env", "log10_C_mass", "f_mass", "U_ground"]

# Starting point from config.py
X0 = [
    G["cover_transmittance"],
    G["envelope_U_W_per_m2K"],
    np.log10(G["mass_heat_capacity_J_per_K"]),
    G["mass_solar_fraction"],
    G["ground_coupling_W_per_K"],
]


def _unpack(x):
    return {
        "tau":      x[0],
        "U_env":    x[1],
        "C_mass":   10 ** x[2],
        "f_mass":   x[3],
        "U_ground": x[4],
    }


def objective(x, rows, quiet=False):
    params = _unpack(x)
    actual    = np.array([r["indoor_temp_f"] for r in rows[1:]])
    predicted = simulate(rows, params)
    residuals = predicted - actual
    rmse = np.sqrt(np.mean(residuals ** 2))
    return rmse


def fit(rows, quiet=False):
    iteration = [0]

    def callback(x):
        iteration[0] += 1
        if not quiet and iteration[0] % 20 == 0:
            rmse = objective(x, rows, quiet=True)
            p = _unpack(x)
            print(f"  iter {iteration[0]:4d}  RMSE={rmse:.3f}°F  "
                  f"tau={p['tau']:.3f}  U_env={p['U_env']:.2f}  "
                  f"C_mass={p['C_mass']:.2e}  f_mass={p['f_mass']:.3f}  "
                  f"U_ground={p['U_ground']:.1f}")

    print("\nFitting parameters (this may take 30–60 seconds)...")
    result = minimize(
        objective,
        X0,
        args=(rows,),
        method="L-BFGS-B",
        bounds=BOUNDS,
        callback=callback,
        options={"maxiter": 2000, "ftol": 1e-9, "gtol": 1e-6},
    )

    return result


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def report(result, rows):
    p = _unpack(result.x)
    actual    = np.array([r["indoor_temp_f"] for r in rows[1:]])
    predicted = simulate(rows, p)
    residuals = predicted - actual
    rmse      = np.sqrt(np.mean(residuals ** 2))
    bias      = np.mean(residuals)

    print("\n" + "=" * 60)
    print("FITTED PARAMETERS")
    print("=" * 60)
    print(f"  cover_transmittance      = {p['tau']:.4f}   (was {G['cover_transmittance']:.4f})")
    print(f"  envelope_U_W_per_m2K     = {p['U_env']:.4f}  (was {G['envelope_U_W_per_m2K']:.4f})")
    print(f"  mass_heat_capacity_J_K   = {p['C_mass']:.3e}  (was {G['mass_heat_capacity_J_per_K']:.3e})")
    print(f"  mass_solar_fraction      = {p['f_mass']:.4f}  (was {G['mass_solar_fraction']:.4f})")
    print(f"  ground_coupling_W_per_K  = {p['U_ground']:.2f}   (was {G['ground_coupling_W_per_K']:.2f})")
    print()
    print(f"FIT QUALITY ({len(actual)} predictions over {len(rows)} readings):")
    print(f"  RMSE      = {rmse:.2f} °F")
    print(f"  Mean bias = {bias:+.2f} °F  (+ = model runs hot, - = model runs cold)")
    print(f"  Max error = {np.max(np.abs(residuals)):.1f} °F")
    print(f"  Optimizer: {'converged' if result.success else 'DID NOT CONVERGE'} ({result.message})")

    # Initial RMSE for comparison
    initial_rmse = objective(X0, rows)
    print(f"\n  Initial RMSE (config.py values) = {initial_rmse:.2f} °F")
    print(f"  Improvement = {initial_rmse - rmse:.2f} °F")

    print("\n" + "=" * 60)
    print("SUGGESTED config.py GREENHOUSE DICT UPDATES:")
    print("=" * 60)
    print(f'    "cover_transmittance":       {p["tau"]:.4f},')
    print(f'    "envelope_U_W_per_m2K":      {p["U_env"]:.4f},')
    print(f'    "mass_heat_capacity_J_per_K": {p["C_mass"]:.1f},')
    print(f'    "mass_solar_fraction":        {p["f_mass"]:.4f},')
    print(f'    "ground_coupling_W_per_K":    {p["U_ground"]:.2f},')
    print()
    print("NOTE: Review these values for physical plausibility before applying.")
    print("  tau should be 0.5–0.90 for single-pane glass (typical ~0.75–0.85)")
    print("  U_env should be 3–7 W/m²K for single-pane (nominal 5.8)")
    print("  C_mass in range 2e6–2e7 J/K is reasonable for this structure")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit greenhouse thermal model parameters")
    parser.add_argument("--days", type=int, default=7, help="Days of history to use (default: 7)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-iteration output")
    args = parser.parse_args()

    rows = load_sensor_data(days_back=args.days)
    if len(rows) < 50:
        print(f"ERROR: Only {len(rows)} rows — need at least 50 for a meaningful fit.")
        sys.exit(1)

    print(f"Data spans {rows[0]['timestamp']} to {rows[-1]['timestamp']}")
    print(f"Indoor temp range: {min(r['indoor_temp_f'] for r in rows):.1f}°F – "
          f"{max(r['indoor_temp_f'] for r in rows):.1f}°F")
    print(f"Solar range: {min(r['solar_irradiance_wm2'] for r in rows):.0f} – "
          f"{max(r['solar_irradiance_wm2'] for r in rows):.0f} W/m²")

    result = fit(rows, quiet=args.quiet)
    report(result, rows)
