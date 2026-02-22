/* ============================================================
   Greenhouse Controls — dashboard.js
   Handles: live state polling, override buttons, charts
   ============================================================ */

"use strict";

// ---------------------------------------------------------------------------
// WMO weather code → icon filename mapping
// Day vs. night determined by comparing current hour to sunrise/sunset
// (sunrise/sunset comes from Open-Meteo hourly data in forecast)
// ---------------------------------------------------------------------------
const WMO_ICON_MAP = {
  0:  { day: "clear-day",           night: "clear-night" },
  1:  { day: "partly-cloudy-day",   night: "partly-cloudy-night" },
  2:  { day: "partly-cloudy-day",   night: "partly-cloudy-night" },
  3:  { day: "overcast",            night: "overcast" },
  45: { day: "fog",                 night: "fog" },
  48: { day: "fog",                 night: "fog" },
  51: { day: "drizzle",             night: "drizzle" },
  53: { day: "drizzle",             night: "drizzle" },
  55: { day: "drizzle",             night: "drizzle" },
  56: { day: "drizzle",             night: "drizzle" },
  57: { day: "drizzle",             night: "drizzle" },
  61: { day: "rain",                night: "rain" },
  63: { day: "rain",                night: "rain" },
  65: { day: "rain",                night: "rain" },
  66: { day: "rain",                night: "rain" },
  67: { day: "rain",                night: "rain" },
  71: { day: "snow",                night: "snow" },
  73: { day: "snow",                night: "snow" },
  75: { day: "snow",                night: "snow" },
  77: { day: "snow",                night: "snow" },
  80: { day: "rain",                night: "rain" },
  81: { day: "rain",                night: "rain" },
  82: { day: "rain",                night: "rain" },
  85: { day: "snow",                night: "snow" },
  86: { day: "snow",                night: "snow" },
  95: { day: "thunderstorm",        night: "thunderstorm" },
  96: { day: "thunderstorm",        night: "thunderstorm" },
  99: { day: "thunderstorm",        night: "thunderstorm" },
};

function wmoIcon(code, isDay) {
  const entry = WMO_ICON_MAP[code] || WMO_ICON_MAP[0];
  return `/static/icons/${isDay ? entry.day : entry.night}.png`;
}

// Simple daytime check: 6am–8pm local time
function isDaytime() {
  const h = new Date().getHours();
  return h >= 6 && h < 20;
}

// ---------------------------------------------------------------------------
// Clock
// ---------------------------------------------------------------------------
function updateClock() {
  const el = document.getElementById("nav-datetime");
  if (!el) return;
  const now = new Date();
  const opts = { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" };
  el.textContent = now.toLocaleString("en-US", opts);
}
setInterval(updateClock, 1000);
updateClock();

// ---------------------------------------------------------------------------
// State polling
// ---------------------------------------------------------------------------
let _lastState = null;
let _fanOverrideExpires = null;   // Date object or null

function startDashboardPolling() {
  fetchState();
  setInterval(fetchState, 30000);
  setInterval(tickFanTimer, 1000);
}

async function fetchState() {
  try {
    const res = await fetch("/api/state");
    const state = await res.json();
    _lastState = state;
    applyState(state);
  } catch (e) {
    console.warn("State fetch failed:", e);
  }
}

function applyState(s) {
  // Controller badge
  const badge = document.getElementById("controller-badge");
  if (badge) {
    badge.className = "controller-badge " + (s.controller_online ? "online" : "offline");
    badge.title = s.controller_online
      ? "Controller online"
      : "Controller offline" + (s.controller_last_seen ? ` — last seen ${formatRelTime(s.controller_last_seen)}` : "");
  }

  // Temperatures
  setText("outdoor-temp", s.outdoor_temp != null ? fmtTemp(s.outdoor_temp) : "—°F");
  setText("outdoor-humidity", s.outdoor_humidity != null ? Math.round(s.outdoor_humidity) : "—");
  setText("indoor-temp",   s.indoor_temp != null ? fmtTemp(s.indoor_temp) : "—°F");
  setText("indoor-humidity", s.indoor_humidity != null ? Math.round(s.indoor_humidity) : "—");

  // Forecast
  if (s.forecast) {
    const fc = s.forecast;
    if (fc.forecast_2h_temp != null) {
      // Open-Meteo returns Celsius — convert to F
      const tempF = celsiusToF(fc.forecast_2h_temp);
      setText("forecast-temp", fmtTemp(tempF));
    }
    // Weather icons
    const day = isDaytime();
    setIcon("icon-current",  wmoIcon(s.forecast.current_code, day));
    setIcon("icon-forecast", wmoIcon(s.forecast.forecast_2h_code, day));
  }

  // Forecast humidity: not directly available, show current outdoor humidity as approximate
  setText("forecast-humidity", s.outdoor_humidity != null ? Math.round(s.outdoor_humidity) : "—");

  // Greenhouse image
  const img = document.getElementById("greenhouse-img");
  if (img) {
    const east = s.shades_east || "open";
    const west = s.shades_west || "open";
    let imgName = "greenhouse-open";
    if (east === "closed" && west === "closed") imgName = "greenhouse-both";
    else if (east === "closed") imgName = "greenhouse-east";
    else if (west === "closed") imgName = "greenhouse-west";
    img.src = `/static/images/${imgName}.png`;
  }

  // Build override lookup
  const overrides = {};
  if (s.overrides) {
    for (const ov of s.overrides) {
      overrides[ov.actuator] = ov;
    }
  }

  // Control buttons
  applyBtn("btn-shades-east", s.shades_east === "closed", overrides["shades_east"]);
  applyBtn("btn-shades-west", s.shades_west === "closed", overrides["shades_west"]);
  applyBtn("btn-fan",        s.fan_on,                   overrides["fan"]);
  applyBtn("btn-circ-fans",  s.circ_fans_on,             overrides["circ_fans"]);

  // HVAC
  const hvacOn = s.hvac_mode && s.hvac_mode !== "off";
  applyBtn("btn-hvac", hvacOn, overrides["hvac"]);
  const hvacBtn = document.getElementById("btn-hvac");
  if (hvacBtn) {
    hvacBtn.classList.toggle("hvac-running", hvacOn);
  }

  // HVAC setpoints
  if (s.settings) {
    setText("heat-setpoint", s.settings.hvac_heat_setpoint + "°");
    setText("cool-setpoint", s.settings.hvac_cool_setpoint + "°");
    // Store for adjustSetpoint()
    window._settings = s.settings;
  }

  // Fan override timer
  const fanOv = overrides["fan"];
  if (fanOv) {
    _fanOverrideExpires = new Date(fanOv.expires_at);
  } else {
    _fanOverrideExpires = null;
    setText("fan-timer", "5:00 min");
  }

  // Gauges (energy page)
  if (s.power_kw != null) {
    updateGauge("gauge-power",   s.power_kw,   "kW",  0, 5);
    updateGauge("gauge-current", s.current_a,  "A",   0, 30);
    updateGauge("gauge-voltage", s.voltage_v,  "V",   200, 260);
  }
}

function applyBtn(id, isOn, override) {
  const btn = document.getElementById(id);
  if (!btn) return;
  // Active = device state is "on" OR there's an active override
  const active = !!isOn;
  btn.classList.toggle("active", active);
  const dot = btn.querySelector(".btn-dot");
  if (dot) dot.style.background = active ? "var(--dot-on)" : "var(--dot-off)";
}

// Fan countdown timer (ticks every second)
function tickFanTimer() {
  const el = document.getElementById("fan-timer");
  if (!el) return;
  if (!_fanOverrideExpires) { el.textContent = "5:00 min"; return; }
  const msLeft = _fanOverrideExpires - Date.now();
  if (msLeft <= 0) {
    el.textContent = "5:00 min";
    _fanOverrideExpires = null;
    return;
  }
  const totalSec = Math.ceil(msLeft / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  el.textContent = `${m}:${String(s).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Override button handlers (attached via onclick in HTML via event delegation)
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".ctrl-btn[data-actuator]").forEach(btn => {
    btn.addEventListener("click", handleOverrideClick);
  });
});

async function handleOverrideClick(e) {
  const btn = e.currentTarget;
  const actuator = btn.dataset.actuator;
  const isActive = btn.classList.contains("active");
  const duration = parseInt(btn.dataset.duration || "120");

  // Toggle: if active, cancel override; if inactive, set override
  if (isActive) {
    // Cancel override → device returns to controller
    try {
      const res = await fetch(`/api/override/${actuator}`, { method: "DELETE" });
      const data = await res.json();
      if (data.ok) {
        btn.classList.remove("active");
        const dot = btn.querySelector(".btn-dot");
        if (dot) dot.style.background = "var(--dot-off)";
      }
    } catch (err) {
      console.error("Cancel override failed:", err);
    }
  } else {
    // Determine command
    let command;
    try {
      command = JSON.parse(btn.dataset.cmdOn || "{}");
    } catch {
      command = {};
    }

    try {
      const res = await fetch("/api/override", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actuator, command, duration_minutes: duration }),
      });
      const data = await res.json();
      if (data.ok) {
        btn.classList.add("active");
        const dot = btn.querySelector(".btn-dot");
        if (dot) dot.style.background = "var(--dot-on)";
        if (actuator === "fan" && data.expires_at) {
          _fanOverrideExpires = new Date(data.expires_at);
        }
      }
    } catch (err) {
      console.error("Set override failed:", err);
    }
  }
}

// ---------------------------------------------------------------------------
// HVAC setpoint spinners
// ---------------------------------------------------------------------------
let _spDebounce = null;

function adjustSetpoint(which, delta) {
  const id = which === "heat" ? "heat-setpoint" : "cool-setpoint";
  const key = which === "heat" ? "hvac_heat_setpoint" : "hvac_cool_setpoint";
  const el = document.getElementById(id);
  if (!el) return;

  const settings = window._settings || {};
  let val = parseInt(settings[key] || (which === "heat" ? 60 : 80));
  val = Math.max(40, Math.min(95, val + delta));
  settings[key] = val;
  window._settings = settings;
  el.textContent = val + "°";

  // Debounce API call
  clearTimeout(_spDebounce);
  _spDebounce = setTimeout(async () => {
    try {
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: val }),
      });
    } catch (err) {
      console.error("Settings update failed:", err);
    }
  }, 600);
}

// ---------------------------------------------------------------------------
// Gauge updater (energy page)
// ---------------------------------------------------------------------------
function updateGauge(id, value, unit, min, max) {
  const valEl = document.getElementById(id);
  if (valEl) {
    if (value == null) { valEl.textContent = "—"; return; }
    valEl.textContent = formatGaugeVal(value, unit);
  }

  // Arc: arc total circumference for a 180° semicircle with r=50 → π*50 ≈ 157
  const arcId = id + "-arc";
  const arcEl = document.getElementById(arcId);
  if (arcEl) {
    const pct = Math.max(0, Math.min(1, (value - min) / (max - min)));
    const arcLen = 157;
    const offset = arcLen - pct * arcLen;
    arcEl.style.strokeDashoffset = offset;
  }

  // Needle (rotate from -90° to +90°)
  const needleId = id + "-needle";
  const needleEl = document.getElementById(needleId);
  if (needleEl) {
    const pct = Math.max(0, Math.min(1, (value - min) / (max - min)));
    const angleDeg = -90 + pct * 180;
    // Pivot at (60, 65) — center bottom of the arc
    needleEl.setAttribute("transform", `rotate(${angleDeg}, 60, 65)`);
  }
}

function formatGaugeVal(val, unit) {
  if (val == null) return "—";
  if (unit === "kW") return val.toFixed(1) + "kW";
  if (unit === "A")  return val.toFixed(1) + "A";
  if (unit === "V")  return Math.round(val) + "V";
  return val + unit;
}

// ---------------------------------------------------------------------------
// History page
// ---------------------------------------------------------------------------
let _tempChart = null;
let _humChart  = null;
let _tempOffset = 0;
let _humOffset  = 0;

function initHistoryPage() {
  fetchState();  // for controller badge
  loadTempChart();
  loadHumChart();
}

async function loadTempChart() {
  const range = document.getElementById("temp-range")?.value || "24h";
  const data = await fetchHistory(range, _tempOffset);
  if (!data.length) { updateDateLabel("temp-date-label", range, _tempOffset); return; }

  const labels  = data.map(r => formatTimestamp(r.timestamp));
  const indoors = data.map(r => r.indoor_temp_f);
  const outdoors = data.map(r => r.outdoor_temp_f);

  // Stats (indoor)
  const vals = indoors.filter(v => v != null);
  if (vals.length) {
    setText("temp-avg", fmtTemp(avg(vals)));
    setText("temp-max", fmtTemp(Math.max(...vals)));
    setText("temp-min", fmtTemp(Math.min(...vals)));
  }

  updateDateLabel("temp-date-label", range, _tempOffset, data);

  const ctx = document.getElementById("temp-chart");
  if (!ctx) return;

  if (_tempChart) _tempChart.destroy();
  _tempChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Indoor °F",
          data: indoors,
          borderColor: "#ef5350",
          backgroundColor: "rgba(239,83,80,0.08)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
        },
        {
          label: "Outdoor °F",
          data: outdoors,
          borderColor: "#4fc3f7",
          backgroundColor: "rgba(79,195,247,0.08)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
        },
      ],
    },
    options: chartOptions("°F"),
  });
}

async function loadHumChart() {
  const range = document.getElementById("hum-range")?.value || "24h";
  const data = await fetchHistory(range, _humOffset);
  if (!data.length) { updateDateLabel("hum-date-label", range, _humOffset); return; }

  const labels   = data.map(r => formatTimestamp(r.timestamp));
  const indoors  = data.map(r => r.indoor_humidity);
  const outdoors = data.map(r => r.outdoor_humidity);

  const vals = indoors.filter(v => v != null);
  if (vals.length) {
    setText("hum-avg", avg(vals).toFixed(1) + " %");
    setText("hum-max", Math.max(...vals).toFixed(1) + " %");
    setText("hum-min", Math.min(...vals).toFixed(1) + " %");
  }

  updateDateLabel("hum-date-label", range, _humOffset, data);

  const ctx = document.getElementById("hum-chart");
  if (!ctx) return;

  if (_humChart) _humChart.destroy();
  _humChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Indoor %",
          data: indoors,
          borderColor: "#4fc3f7",
          backgroundColor: "rgba(79,195,247,0.08)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: true,
        },
        {
          label: "Outdoor %",
          data: outdoors,
          borderColor: "#80cbc4",
          backgroundColor: "rgba(128,203,196,0.05)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
        },
      ],
    },
    options: chartOptions("%"),
  });
}

function shiftTempRange(dir) { _tempOffset += dir; loadTempChart(); }
function shiftHumRange(dir)  { _humOffset  += dir; loadHumChart();  }

async function fetchHistory(range, offset) {
  try {
    const res = await fetch(`/api/history?range=${range}&offset=${offset}`);
    return await res.json();
  } catch { return []; }
}

// ---------------------------------------------------------------------------
// Energy page
// ---------------------------------------------------------------------------
let _powerChart = null;
let _powerOffset = 0;

function initEnergyPage() {
  loadPowerChart();
}

async function loadPowerChart() {
  const range = document.getElementById("power-range")?.value || "24h";
  const data = await fetchPower(range, _powerOffset);
  updateDateLabel("power-date-label", range, _powerOffset, data);

  const ctx = document.getElementById("power-chart");
  if (!ctx) return;

  const labels = data.map(r => formatTimestamp(r.timestamp));
  const phaseA = data.map(r => r.power_a_kw);
  const phaseB = data.map(r => r.power_b_kw);
  const total  = data.map(r => r.power_total_kw);

  if (_powerChart) _powerChart.destroy();
  _powerChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Phase A kW", data: phaseA, borderColor: "#ce93d8", borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false },
        { label: "Phase B kW", data: phaseB, borderColor: "#f0a030", borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false },
        { label: "Total kW",   data: total,  borderColor: "#4fc3f7", borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false },
      ],
    },
    options: chartOptions("kW"),
  });
}

function shiftPowerRange(dir) { _powerOffset += dir; loadPowerChart(); }

async function fetchPower(range, offset) {
  try {
    const res = await fetch(`/api/power?range=${range}&offset=${offset}`);
    return await res.json();
  } catch { return []; }
}

// ---------------------------------------------------------------------------
// Diagnostic page
// ---------------------------------------------------------------------------
let _diagChart = null;

function initDiagnosticPage() {
  fetchState();
  loadDiagChart();
}

async function loadDiagChart() {
  const range = document.getElementById("diag-range")?.value || "24h";
  try {
    const res = await fetch(`/api/model_accuracy?range=${range}`);
    const data = await res.json();

    const accuracy = data.accuracy || [];
    if (!accuracy.length) return;

    const labels    = accuracy.map(r => formatTimestamp(r.timestamp));
    const actual    = accuracy.map(r => r.actual_temp_f);
    const predicted = accuracy.map(r => r.predicted_temp_f);

    // Stats
    const errors = accuracy.map(r => r.error_f).filter(v => v != null);
    if (errors.length) {
      setText("diag-rmse", Math.sqrt(errors.reduce((s,e) => s+e*e, 0)/errors.length).toFixed(2) + "°F");
      setText("diag-bias", (errors.reduce((s,e) => s+e, 0)/errors.length).toFixed(2) + "°F");
      setText("diag-n",    errors.length);
    }

    const ctx = document.getElementById("diag-chart");
    if (!ctx) return;
    if (_diagChart) _diagChart.destroy();
    _diagChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "Actual °F",    data: actual,    borderColor: "#4fc3f7", borderWidth: 2, pointRadius: 0, tension: 0.3 },
          { label: "Predicted °F", data: predicted, borderColor: "#ef5350", borderWidth: 2, pointRadius: 0, tension: 0.3, borderDash: [6,3] },
        ],
      },
      options: chartOptions("°F"),
    });
  } catch (e) {
    console.warn("Diagnostic data fetch failed:", e);
  }
}

// ---------------------------------------------------------------------------
// Shared Chart.js defaults
// ---------------------------------------------------------------------------
function chartOptions(unit) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: {
        labels: { color: "#9a9a9a", font: { size: 11 }, boxWidth: 18 },
      },
      tooltip: {
        backgroundColor: "#1e1e1e",
        borderColor: "#4a4a4a",
        borderWidth: 1,
        titleColor: "#9a9a9a",
        bodyColor: "#ffffff",
        callbacks: {
          label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) : "—"} ${unit}`,
        },
      },
    },
    scales: {
      x: {
        ticks: { color: "#9a9a9a", maxTicksLimit: 8, maxRotation: 0, font: { size: 11 } },
        grid:  { color: "rgba(255,255,255,0.06)" },
      },
      y: {
        ticks: { color: "#9a9a9a", font: { size: 11 } },
        grid:  { color: "rgba(255,255,255,0.06)" },
      },
    },
  };
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function setIcon(id, src) {
  const el = document.getElementById(id);
  if (el) el.src = src;
}

function fmtTemp(f) {
  return Math.round(f) + "°F";
}

function celsiusToF(c) {
  return c * 9/5 + 32;
}

function avg(arr) {
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function formatTimestamp(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts.endsWith("Z") ? ts : ts + "Z");
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch { return ts; }
}

function formatRelTime(ts) {
  try {
    const d = new Date(ts.endsWith("Z") ? ts : ts + "Z");
    const mins = Math.round((Date.now() - d) / 60000);
    if (mins < 2) return "just now";
    if (mins < 60) return `${mins}m ago`;
    return `${Math.round(mins/60)}h ago`;
  } catch { return ts; }
}

function updateDateLabel(id, range, offset, data) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!data || !data.length) { el.textContent = "no data"; return; }
  try {
    const first = new Date(data[0].timestamp + (data[0].timestamp.endsWith("Z") ? "" : "Z"));
    const last  = new Date(data[data.length-1].timestamp + (data[data.length-1].timestamp.endsWith("Z") ? "" : "Z"));
    const fmt = { month: "2-digit", day: "2-digit", year: "2-digit" };
    if (range === "1h" || range === "24h") {
      // show time
      el.textContent = first.toLocaleDateString("en-US", fmt) + " – " + last.toLocaleDateString("en-US", fmt);
    } else {
      el.textContent = first.toLocaleDateString("en-US", fmt) + " – " + last.toLocaleDateString("en-US", fmt);
    }
  } catch {
    el.textContent = "—";
  }
}
