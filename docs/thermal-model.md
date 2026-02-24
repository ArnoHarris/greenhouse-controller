# Thermal Model & Greenhouse Physical Specs

## Greenhouse Physical Specs

### Dimensions
- Floorplate: 29' × 14.75' (427.75 ft² / 39.74 m²)
- Eave height: 7.6', ridge height: 13.1', roof pitch: 8.5/12
- Rafter length: 9.20' (calculated from pitch)
- Enclosed volume: 4,426 ft³ (125.3 m³)
- Ridge heading: ~295° (WNW). Sides labeled N/S/E/W but building is rotated ~25° CCW from true cardinal directions. "East" roof face actually faces ~NNE, "west" face ~SSW.

### Envelope
- **Glazing:** 6mm single-pane tempered glass (U ≈ 5.8 W/m²K, τ ≈ 0.82)
- **East roof:** 29' × 9.20' = 266.8 ft² (24.8 m²)
- **West roof:** 29' × 9.20' = 266.8 ft² (24.8 m²)
- **East side wall:** 29' × 7.6' = 220.4 ft² (20.5 m²)
- **West side wall:** 29' × 7.6' = 220.4 ft² (20.5 m²)
- **South gable:** 152.7 ft² (14.2 m²)
- **North gable:** 152.7 ft² (14.2 m²) — shared with flower shed (buffer zone)
- Total outdoor-exposed: 104.7 m² (excludes north wall)

### Foundation & Floor
- 2' × 8" concrete perimeter wall (structural, significant thermal mass)
- Mixed floor: concrete center aisle, gravel beds on sides

### Ventilation
- 6 ridge vents + 6 side vents (hydraulic, temperature-actuated — passive)
- 2× 14" exhaust fans on south gable (~2000 cfm total, 0.94 m³/s)
- 4× 2'×2' louver vents on north gable (fan intake)

### Adjacent Structure (Flower Shed)
- North gable connects to unconditioned "flower shed"
- Concrete floor, open east side, partial shelter
- Exhaust fans pull outdoor air through flower shed before entering greenhouse
- Provides pre-cooling effect in summer (concrete thermal mass + shade)

### HVAC
- Mitsubishi MSZ-WR18NA 1.5-ton (18,000 BTU) minisplit on north gable wall

## Thermal Model Design

### Structure
Lumped-parameter system, 2–3 nodes, Euler integration at ~1-minute steps:
- **Air node** (required): indoor air temperature
- **Soil/thermal mass node** (required): thermal flywheel, absorbs heat during day, releases at night
- **Canopy node** (optional, add if significant plant loading)

### Air Node Energy Balance
```
C_air * dT_air/dt = Q_solar_transmitted
                    - U_envelope * A * (T_air - T_outside)
                    - Q_ventilation
                    - Q_latent
                    + Q_HVAC
                    + Q_ground_exchange
```

- `Q_solar_transmitted = I_solar * tau_cover * (roof_solar_east + roof_solar_west + wall_solar)`
  - East/west roof shades reduce solar on their respective face independently
- `Q_ventilation = rho * c_p * fan_flow * (T_air - T_outside)`, fan_flow = 0.94 m³/s when fans on
- `tau_cover` = 0.82 for 6mm single-pane glass

Config.py contains starting parameter estimates from geometry. Empirical tuning will outperform theoretical values due to infiltration, thermal bridges, ground coupling uncertainty, and microclimate effects.

### Thermal Model / Control Logic Separation
- **Thermal model:** current state + forecast → predicted temperature trajectories
- **Control logic:** trajectories → actuator decisions
- Separation allows tuning thresholds independently from model parameters, and visualizing predictions even during manual override

### Calibration Strategy

**Online accuracy tracking (every cycle):**
- Compare the 5-minute-ahead prediction from the previous cycle against actual indoor temp
- Log predicted vs. actual to `model_accuracy` SQLite table
- Compute rolling RMSE and mean bias over 24h/7d windows
- Display on diagnostic dashboard page

**Offline parameter fitting (periodic, via `fit_model.py`):**
- Fit U_envelope*A, thermal mass capacity, ground coupling, solar fraction to minimize prediction error over 1–2 week data window
- Use `scipy.optimize.minimize` with parameter bounds
- Parameters change slowly (weeks/months): plant growth, cover degradation, seasonal sun angles
- Manual fitting only for now — results must be inspected before applying

No continuous auto-tuning: a sensor glitch or unusual event (door left open, equipment failure) could corrupt parameters. Outlier rejection adds complexity not justified until manual process proves the model is sound.

### Forecast Bias Correction
Each control cycle:
1. Compare Open-Meteo's current-hour prediction against actual AmbientWeather station readings
2. Calculate delta (flat delta approach for v1)
3. Apply delta uniformly to forecast's next few hours
4. Use corrected forecast as input to thermal model

Flat delta is a known simplification (forecast error grows with horizon). Revisit with tapered correction if v1 accuracy proves insufficient.

### Early Data Collection Strategy
Connect sensors early — before actuators or control logic are ready — to:
- Fit and validate thermal model coefficients against observed indoor temperature trajectories
- Evaluate Open-Meteo forecast accuracy over different time horizons
- Assess bias correction effectiveness
- Build confidence in model predictions before enabling automated control
