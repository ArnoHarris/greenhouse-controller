/* ============================================================
   Greenhouse Controls — dashboard.js
   ============================================================ */

"use strict";

// ---------------------------------------------------------------------------
// WMO weather code → icon filename
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
  const opts = { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" };
  el.textContent = new Date().toLocaleString("en-US", opts);
}
setInterval(updateClock, 1000);
updateClock();

// ---------------------------------------------------------------------------
// State polling
// ---------------------------------------------------------------------------
let _lastState = null;
let _fanOverrideExpires = null;

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
  setText("outdoor-temp",     s.outdoor_temp     != null ? fmtTemp(s.outdoor_temp)          : "—°F");
  setText("outdoor-humidity", s.outdoor_humidity != null ? Math.round(s.outdoor_humidity)    : "—");
  setText("indoor-temp",      s.indoor_temp      != null ? fmtTemp(s.indoor_temp)            : "—°F");
  setText("indoor-humidity",  s.indoor_humidity  != null ? Math.round(s.indoor_humidity)     : "—");

  // Forecast icons + arrow temp — use Open-Meteo's is_day for accurate day/night icons
  if (s.forecast) {
    const fc = s.forecast;
    if (fc.forecast_2h_temp != null) setText("forecast-temp", fmtTemp(fc.forecast_2h_temp));
    const dayNow = fc.current_is_day  !== undefined ? fc.current_is_day  : isDaytime();
    const dayFwd = fc.forecast_2h_is_day !== undefined ? fc.forecast_2h_is_day : isDaytime();
    setIcon("icon-current",  wmoIcon(fc.current_code,     dayNow));
    setIcon("icon-forecast", wmoIcon(fc.forecast_2h_code, dayFwd));
  }
  setText("forecast-humidity", s.outdoor_humidity != null ? Math.round(s.outdoor_humidity) : "—");

  // Build override lookup
  const overrides = {};
  if (s.overrides) {
    for (const ov of s.overrides) overrides[ov.actuator] = ov;
  }

  // Greenhouse image — use override command when active
  const img = document.getElementById("greenhouse-img");
  if (img) {
    const eastOv = overrides["shades_east"];
    const westOv = overrides["shades_west"];
    const east = eastOv ? (tryParseCmd(eastOv.command).position || s.shades_east || "open") : (s.shades_east || "open");
    const west = westOv ? (tryParseCmd(westOv.command).position || s.shades_west || "open") : (s.shades_west || "open");
    let imgName = "greenhouse-open";
    if (east === "closed" && west === "closed") imgName = "greenhouse-both";
    else if (east === "closed") imgName = "greenhouse-east";
    else if (west === "closed") imgName = "greenhouse-west";
    img.src = `/static/images/${imgName}.png`;
  }

  // Control buttons
  applyBtn("btn-shades-east", s.shades_east === "closed", overrides["shades_east"]);
  applyBtn("btn-shades-west", s.shades_west === "closed", overrides["shades_west"]);
  applyBtn("btn-fan",         s.fan_on,                   overrides["fan"]);
  applyBtn("btn-circ-fans",   s.circ_fans_on,             overrides["circ_fans"]);

  // HVAC
  const hvacOn = s.hvac_mode && s.hvac_mode !== "off";
  applyBtn("btn-hvac", hvacOn, overrides["hvac"]);
  const hvacBtn = document.getElementById("btn-hvac");
  if (hvacBtn) hvacBtn.classList.toggle("hvac-running", hvacOn);

  // HVAC setpoints
  if (s.settings) {
    setText("heat-setpoint", s.settings.hvac_heat_setpoint + "°");
    setText("cool-setpoint", s.settings.hvac_cool_setpoint + "°");
    window._settings = s.settings;
  }

  // Fan override timer — only count down when override is for ON state
  const fanOv = overrides["fan"];
  if (fanOv) {
    const fanCmd = tryParseCmd(fanOv.command);
    if (fanCmd.on) {
      _fanOverrideExpires = new Date(fanOv.expires_at);
    } else {
      _fanOverrideExpires = null;
      setText("fan-timer", "5:00 min");
    }
  } else {
    _fanOverrideExpires = null;
    setText("fan-timer", "5:00 min");
  }

  // Gauges (energy page — no-ops on other pages)
  drawGauge("gauge-freq-svg",    "gauge-freq",    s.freq_hz,   GAUGE_SPECS.freq);
  drawGauge("gauge-power-svg",   "gauge-power",   s.power_kw,  GAUGE_SPECS.power);
  drawGauge("gauge-current-svg", "gauge-current", s.current_a, GAUGE_SPECS.current);
  drawGauge("gauge-voltage-svg", "gauge-voltage", s.voltage_v, GAUGE_SPECS.voltage);
}

function tryParseCmd(json) {
  try { return JSON.parse(json || "{}"); } catch { return {}; }
}

// ---------------------------------------------------------------------------
// Button state
// ---------------------------------------------------------------------------
function applyBtn(id, isOn, override) {
  const btn = document.getElementById(id);
  if (!btn) return;
  let active;
  if (override) {
    const cmd = tryParseCmd(override.command);
    if ("position" in cmd) active = cmd.position === "closed";
    else if ("on"   in cmd) active = !!cmd.on;
    else if ("mode" in cmd) active = cmd.mode !== "off";
    else active = !!isOn;
  } else {
    active = !!isOn;
  }
  btn.classList.toggle("active", active);
  const dot = btn.querySelector(".btn-dot");
  if (dot) dot.style.background = active ? "var(--dot-on)" : "var(--dot-off)";
}

// ---------------------------------------------------------------------------
// Fan countdown timer
// ---------------------------------------------------------------------------
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
// Override button handlers
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".ctrl-btn[data-actuator]").forEach(btn => {
    btn.addEventListener("click", handleOverrideClick);
  });
  document.addEventListener("pointermove",   tapeDragMove);
  document.addEventListener("pointerup",     tapeDragEnd);
  document.addEventListener("pointercancel", tapeDragEnd);
});

async function handleOverrideClick(e) {
  const btn       = e.currentTarget;
  const actuator  = btn.dataset.actuator;
  const isActive  = btn.classList.contains("active");
  const duration  = parseInt(btn.dataset.duration || "120");
  // Always POST a new override (on or off). The backend's "latest wins" logic
  // cancels any previous override for this actuator and creates a new one.
  // This prevents fetchState from re-activating the button via stale device state.
  const cmdJson   = isActive ? (btn.dataset.cmdOff || "{}") : (btn.dataset.cmdOn || "{}");
  const command   = tryParseCmd(cmdJson);
  const newActive = !isActive;

  try {
    const res  = await fetch("/api/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actuator, command, duration_minutes: duration }),
    });
    const data = await res.json();
    if (data.ok) {
      btn.classList.toggle("active", newActive);
      const dot = btn.querySelector(".btn-dot");
      if (dot) dot.style.background = newActive ? "var(--dot-on)" : "var(--dot-off)";
      if (actuator === "fan") {
        if (newActive && data.expires_at) {
          _fanOverrideExpires = new Date(data.expires_at);
        } else {
          _fanOverrideExpires = null;
          setText("fan-timer", "5:00 min");
        }
      }
      fetchState();
    }
  } catch (err) { console.error("Override toggle failed:", err); }
}

// ---------------------------------------------------------------------------
// Tape picker — HVAC setpoint
// ---------------------------------------------------------------------------
const TAPE_MIN    = 40;
const TAPE_MAX    = 95;
const TAPE_ITEM_H = 52;     // must match CSS .tape-item height
const TAPE_CTR_Y  = 130;    // overlay height / 2

let _tapeWhich         = null;
let _tapeDragging      = false;
let _tapeDragStartY    = 0;
let _tapeStartOffset   = 0;
let _tapeCurrentOffset = 0;

function tapeOffsetForValue(val) {
  const i = val - TAPE_MIN;
  return TAPE_CTR_Y - TAPE_ITEM_H / 2 - i * TAPE_ITEM_H;
}

function valueFromOffset(offset) {
  const i = (TAPE_CTR_Y - TAPE_ITEM_H / 2 - offset) / TAPE_ITEM_H;
  return Math.max(TAPE_MIN, Math.min(TAPE_MAX, Math.round(i) + TAPE_MIN));
}

function openTape(which, anchorEl) {
  _tapeWhich = which;
  const settings = window._settings || {};
  const key = which === "heat" ? "hvac_heat_setpoint" : "hvac_cool_setpoint";
  const val = parseInt(settings[key] || (which === "heat" ? 60 : 80));

  // Build items
  const container = document.getElementById("tape-items");
  container.innerHTML = "";
  for (let v = TAPE_MIN; v <= TAPE_MAX; v++) {
    const item = document.createElement("div");
    item.className = "tape-item" + (v === val ? " selected" : "");
    item.textContent = v + "°";
    item.dataset.val = v;
    container.appendChild(item);
  }

  // Position tape
  _tapeCurrentOffset = tapeOffsetForValue(val);
  container.style.transition = "";
  container.style.transform  = `translateY(${_tapeCurrentOffset}px)`;

  // Position overlay centered over the button that was pressed
  const tape    = document.getElementById("setpoint-tape");
  const btnRect = anchorEl.getBoundingClientRect();
  const tLeft   = Math.max(0, Math.min(btnRect.left + btnRect.width / 2 - 50, window.innerWidth - 116));
  const tTop    = Math.max(0, btnRect.top + btnRect.height / 2 - 130);
  tape.className   = "tape-overlay tape-" + which;
  tape.style.left  = tLeft + "px";
  tape.style.top   = tTop  + "px";
  tape.style.display = "block";
  document.getElementById("tape-backdrop").style.display = "block";

  container.addEventListener("pointerdown", tapeDragStart);
}

function tapeDragStart(e) {
  _tapeDragging   = true;
  _tapeDragStartY = e.clientY;
  _tapeStartOffset = _tapeCurrentOffset;
  e.currentTarget.setPointerCapture(e.pointerId);
  e.preventDefault();
}

function tapeDragMove(e) {
  if (!_tapeDragging) return;
  const delta = e.clientY - _tapeDragStartY;
  _tapeCurrentOffset = _tapeStartOffset + delta;
  const container = document.getElementById("tape-items");
  if (!container) return;
  container.style.transform = `translateY(${_tapeCurrentOffset}px)`;
  const val = valueFromOffset(_tapeCurrentOffset);
  container.querySelectorAll(".tape-item").forEach(item => {
    item.classList.toggle("selected", parseInt(item.dataset.val) === val);
  });
}

function tapeDragEnd(e) {
  if (!_tapeDragging) return;
  _tapeDragging = false;
  const val = valueFromOffset(_tapeCurrentOffset);
  commitTapeValue(val);
}

async function commitTapeValue(val) {
  const which = _tapeWhich;
  const key   = which === "heat" ? "hvac_heat_setpoint" : "hvac_cool_setpoint";
  const id    = which === "heat" ? "heat-setpoint"      : "cool-setpoint";

  // Enforce 10° minimum gap between heat and cool setpoints
  const s = window._settings || {};
  if (which === "heat") {
    const cool = parseInt(s.hvac_cool_setpoint || 80);
    val = Math.min(val, cool - 10);
  } else {
    const heat = parseInt(s.hvac_heat_setpoint || 60);
    val = Math.max(val, heat + 10);
  }
  val = Math.max(TAPE_MIN, Math.min(TAPE_MAX, val));

  setText(id, val + "°");

  // Snap with transition
  _tapeCurrentOffset = tapeOffsetForValue(val);
  const container = document.getElementById("tape-items");
  if (container) {
    container.style.transition = "transform 0.15s";
    container.style.transform  = `translateY(${_tapeCurrentOffset}px)`;
    container.querySelectorAll(".tape-item").forEach(item => {
      item.classList.toggle("selected", parseInt(item.dataset.val) === val);
    });
    setTimeout(() => { if (container) container.style.transition = ""; }, 200);
  }

  // Update settings
  const settings = window._settings || {};
  settings[key] = val;
  window._settings = settings;

  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: val }),
    });
  } catch (err) { console.error("Settings update failed:", err); }

  setTimeout(() => closeTape(false), 400);
}

function closeTape() {
  const tape     = document.getElementById("setpoint-tape");
  const backdrop = document.getElementById("tape-backdrop");
  if (tape)     tape.style.display     = "none";
  if (backdrop) backdrop.style.display = "none";
  _tapeWhich    = null;
  _tapeDragging = false;
}

// ---------------------------------------------------------------------------
// Gauge drawing (energy page)
// ---------------------------------------------------------------------------
const SVG_NS = "http://www.w3.org/2000/svg";
const GAUGE_R  = 50;
const GAUGE_CX = 60;
const GAUGE_CY = 65;
const GAUGE_SW = 12;

const GAUGE_SPECS = {
  freq:    { min: 59,  max: 61,  unit: "Hz", dec: 2,
    zones: [{a:59,  b:59.5, c:"#e03030"},{a:59.5,b:60.5,c:"#3fdc3f"},{a:60.5,b:61,  c:"#e03030"}] },
  power:   { min: 0,   max: 16,  unit: "kW", dec: 1,
    zones: [{a:0,   b:10,   c:"#3fdc3f"},{a:10,  b:14,  c:"#f0c040"},{a:14,  b:16,  c:"#e03030"}] },
  current: { min: 0,   max: 70,  unit: "A",  dec: 1,
    zones: [{a:0,   b:45,   c:"#3fdc3f"},{a:45,  b:55,  c:"#f0c040"},{a:55,  b:70,  c:"#e03030"}] },
  voltage: { min: 215, max: 265, unit: "V",  dec: 0,
    zones: [{a:215, b:228,  c:"#e03030"},{a:228, b:252, c:"#3fdc3f"},{a:252, b:265, c:"#e03030"}] },
};

function gaugeArcPoint(frac) {
  const angle = Math.PI * (1 - frac);
  return { x: GAUGE_CX + GAUGE_R * Math.cos(angle), y: GAUGE_CY - GAUGE_R * Math.sin(angle) };
}

function gaugeArcPath(f1, f2) {
  const p1 = gaugeArcPoint(f1);
  const p2 = gaugeArcPoint(f2);
  return `M ${p1.x.toFixed(2)},${p1.y.toFixed(2)} A ${GAUGE_R} ${GAUGE_R} 0 0 1 ${p2.x.toFixed(2)},${p2.y.toFixed(2)}`;
}

function mkSvgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function drawGauge(svgId, valId, value, spec) {
  const svg = document.getElementById(svgId);
  if (!svg) return;

  const valEl = document.getElementById(valId);
  if (valEl) {
    valEl.textContent = value != null
      ? (spec.dec === 0 ? Math.round(value) : value.toFixed(spec.dec)) + "\u00a0" + spec.unit
      : "—";
  }

  while (svg.firstChild) svg.removeChild(svg.firstChild);

  // Background track (split at midpoint to avoid 180° SVG ambiguity)
  const mid = gaugeArcPoint(0.5);
  svg.appendChild(mkSvgEl("path", {
    d: `M 10,65 A ${GAUGE_R} ${GAUGE_R} 0 0 1 ${mid.x.toFixed(2)},${mid.y.toFixed(2)} A ${GAUGE_R} ${GAUGE_R} 0 0 1 110,65`,
    stroke: "#3a3a3a", "stroke-width": GAUGE_SW + 2, "stroke-linecap": "butt", fill: "none",
  }));

  // Colored zones
  const range = spec.max - spec.min;
  for (const z of spec.zones) {
    svg.appendChild(mkSvgEl("path", {
      d: gaugeArcPath((z.a - spec.min) / range, (z.b - spec.min) / range),
      stroke: z.c, "stroke-width": GAUGE_SW, "stroke-linecap": "butt", fill: "none",
    }));
  }

  // Needle + center dot
  if (value != null) {
    const frac = Math.max(0, Math.min(1, (value - spec.min) / range));
    const pt   = gaugeArcPoint(frac);
    svg.appendChild(mkSvgEl("line", {
      x1: GAUGE_CX, y1: GAUGE_CY, x2: pt.x.toFixed(2), y2: pt.y.toFixed(2),
      stroke: "white", "stroke-width": 2, "stroke-linecap": "round",
    }));
    svg.appendChild(mkSvgEl("circle", { cx: GAUGE_CX, cy: GAUGE_CY, r: 4, fill: "white" }));
  }
}

// ---------------------------------------------------------------------------
// History page
// ---------------------------------------------------------------------------
let _tempChart  = null;
let _humChart   = null;
let _tempOffset = 0;
let _humOffset  = 0;

function initHistoryPage() {
  fetchState();
  loadTempChart();
  loadHumChart();
}

async function loadTempChart() {
  const range = document.getElementById("temp-range")?.value || "24h";
  const data  = await fetchHistory(range, _tempOffset);
  updateDateLabel("temp-date-label", data);
  if (!data.length) return;

  const indoor  = data.map(r => ({ x: new Date(r.timestamp), y: r.indoor_temp_f  }));
  const outdoor = data.map(r => ({ x: new Date(r.timestamp), y: r.outdoor_temp_f }));

  const inVals  = data.map(r => r.indoor_temp_f).filter(v => v != null);
  const outVals = data.map(r => r.outdoor_temp_f).filter(v => v != null);
  if (inVals.length) {
    setText("temp-avg-in",  fmtTemp(avg(inVals)));
    setText("temp-max-in",  fmtTemp(Math.max(...inVals)));
    setText("temp-min-in",  fmtTemp(Math.min(...inVals)));
  }
  if (outVals.length) {
    setText("temp-avg-out", fmtTemp(avg(outVals)));
    setText("temp-max-out", fmtTemp(Math.max(...outVals)));
    setText("temp-min-out", fmtTemp(Math.min(...outVals)));
  }

  const ctx = document.getElementById("temp-chart");
  if (!ctx) return;
  if (_tempChart) _tempChart.destroy();
  _tempChart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        { label: "Indoor °F",  data: indoor,  borderColor: "#ef5350", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
        { label: "Outdoor °F", data: outdoor, borderColor: "#4fc3f7", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
      ],
    },
    options: chartOptions(range, "°F"),
  });
}

async function loadHumChart() {
  const range = document.getElementById("hum-range")?.value || "24h";
  const data  = await fetchHistory(range, _humOffset);
  updateDateLabel("hum-date-label", data);
  if (!data.length) return;

  const indoor  = data.map(r => ({ x: new Date(r.timestamp), y: r.indoor_humidity  }));
  const outdoor = data.map(r => ({ x: new Date(r.timestamp), y: r.outdoor_humidity }));

  const inVals  = data.map(r => r.indoor_humidity).filter(v => v != null);
  const outVals = data.map(r => r.outdoor_humidity).filter(v => v != null);
  if (inVals.length) {
    setText("hum-avg-in",  avg(inVals).toFixed(1) + " %");
    setText("hum-max-in",  Math.max(...inVals).toFixed(1) + " %");
    setText("hum-min-in",  Math.min(...inVals).toFixed(1) + " %");
  }
  if (outVals.length) {
    setText("hum-avg-out", avg(outVals).toFixed(1) + " %");
    setText("hum-max-out", Math.max(...outVals).toFixed(1) + " %");
    setText("hum-min-out", Math.min(...outVals).toFixed(1) + " %");
  }

  const ctx = document.getElementById("hum-chart");
  if (!ctx) return;
  if (_humChart) _humChart.destroy();
  _humChart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        { label: "Indoor %",  data: indoor,  borderColor: "#4fc3f7", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
        { label: "Outdoor %", data: outdoor, borderColor: "#80cbc4", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
      ],
    },
    options: chartOptions(range, "%"),
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
let _powerChart  = null;
let _powerOffset = 0;

function initEnergyPage() {
  drawGauge("gauge-freq-svg",    "gauge-freq",    null, GAUGE_SPECS.freq);
  drawGauge("gauge-power-svg",   "gauge-power",   null, GAUGE_SPECS.power);
  drawGauge("gauge-current-svg", "gauge-current", null, GAUGE_SPECS.current);
  drawGauge("gauge-voltage-svg", "gauge-voltage", null, GAUGE_SPECS.voltage);
  loadPowerChart();
}

async function loadPowerChart() {
  const range = document.getElementById("power-range")?.value || "24h";
  const data  = await fetchPower(range, _powerOffset);
  updateDateLabel("power-date-label", data);
  if (!data.length) return;

  const phaseA = data.map(r => ({ x: new Date(r.timestamp), y: r.power_a_kw     }));
  const phaseB = data.map(r => ({ x: new Date(r.timestamp), y: r.power_b_kw     }));
  const total  = data.map(r => ({ x: new Date(r.timestamp), y: r.power_total_kw }));

  const ctx = document.getElementById("power-chart");
  if (!ctx) return;
  if (_powerChart) _powerChart.destroy();
  _powerChart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        { label: "Phase A kW", data: phaseA, borderColor: "#ce93d8", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
        { label: "Phase B kW", data: phaseB, borderColor: "#f0a030", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
        { label: "Total kW",   data: total,  borderColor: "#4fc3f7", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
      ],
    },
    options: chartOptions(range, "kW"),
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
let _diagTempChart    = null;
let _solarChart       = null;
let _timelineChart    = null;
let _hvacRuntimeChart = null;
let _diagPowerChart   = null;
let _diagPowerOffset  = 0;

const ACTUATOR_ORDER  = ["East Shades", "West Shades", "Exhaust Fans", "Circ Fans", "HVAC"];
const ACTUATOR_COLORS = {
  "East Shades": "#ce93d8",
  "West Shades": "#f48fb1",
  "Exhaust Fans": "#80deea",
  "Circ Fans": "#a5d6a7",
  "HVAC": "#f0a030",
};

function initDiagnosticPage() {
  fetchState();
  loadDiagCharts();
  loadDiagPowerChart();
}

function loadDiagCharts() {
  const range = document.getElementById("diag-range")?.value || "24h";
  loadDiagTempChart(range);
  loadSolarChart(range);
  loadTimelineChart(range);
  loadHvacRuntimeChart(range);
}

async function loadDiagTempChart(range) {
  try {
    const res      = await fetch(`/api/model_accuracy?range=${range}`);
    const payload  = await res.json();
    const accuracy = payload.accuracy || [];

    const errors = accuracy.map(r => r.error_f).filter(v => v != null);
    if (errors.length) {
      setText("diag-rmse", Math.sqrt(errors.reduce((s, e) => s + e * e, 0) / errors.length).toFixed(2) + "°F");
      setText("diag-bias", (errors.reduce((s, e) => s + e, 0) / errors.length).toFixed(2) + "°F");
      setText("diag-n",    errors.length);
    }

    const ctx = document.getElementById("diag-chart");
    if (!ctx || !accuracy.length) return;
    if (_diagTempChart) _diagTempChart.destroy();
    _diagTempChart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [
          { label: "Actual °F",    data: accuracy.map(r => ({ x: new Date(r.timestamp), y: r.actual_temp_f    })), borderColor: "#4fc3f7", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
          { label: "Predicted °F", data: accuracy.map(r => ({ x: new Date(r.timestamp), y: r.predicted_temp_f })), borderColor: "#ef5350", borderWidth: 2, pointRadius: 0, tension: 0, fill: false, borderDash: [6, 3] },
        ],
      },
      options: chartOptions(range, "°F"),
    });
  } catch (e) { console.warn("Diag temp chart failed:", e); }
}

async function loadSolarChart(range) {
  try {
    const res    = await fetch(`/api/solar_forecast?range=${range}`);
    const data   = await res.json();
    const actual   = (data.actual   || []).map(r => ({ x: new Date(r.timestamp), y: r.solar_irradiance_wm2 }));
    const forecast = (data.forecast || []).map(r => ({ x: new Date(r.timestamp), y: r.solar_wm2 }));

    const ctx = document.getElementById("solar-chart");
    if (!ctx) return;
    if (_solarChart) _solarChart.destroy();
    _solarChart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [
          { label: "Actual W/m²",   data: actual,   borderColor: "#f0c040", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
          { label: "Forecast W/m²", data: forecast, borderColor: "#888",    borderWidth: 2, pointRadius: 0, tension: 0, fill: false, borderDash: [6, 3] },
        ],
      },
      options: chartOptions(range, "W/m²"),
    });
  } catch (e) { console.warn("Solar chart failed:", e); }
}

async function loadTimelineChart(range) {
  try {
    const res  = await fetch(`/api/actuator_timeline?range=${range}`);
    const data = await res.json();

    const datasets = ACTUATOR_ORDER.map(name => ({
      label:           name,
      data:            (data[name] || []).map(p => ({ x: [new Date(p.start), new Date(p.end)], y: name })),
      backgroundColor: ACTUATOR_COLORS[name] || "#888",
      borderWidth:     0,
      borderRadius:    2,
      barThickness:    14,
    }));

    const ctx = document.getElementById("timeline-chart");
    if (!ctx) return;
    if (_timelineChart) _timelineChart.destroy();
    const ts = timeScaleConfig(range);
    _timelineChart = new Chart(ctx, {
      type: "bar",
      data: { datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        indexAxis: "y",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => {
                const d = ctx.raw;
                if (d && d.x && Array.isArray(d.x)) {
                  return ` ${fmtShortTime(d.x[0])} – ${fmtShortTime(d.x[1])}`;
                }
                return "";
              },
            },
          },
        },
        scales: {
          x: {
            type: "time",
            time: { unit: ts.unit, stepSize: ts.stepSize, displayFormats: ts.displayFormats },
            ticks: { color: "#9a9a9a", maxRotation: 0, font: { size: 11 } },
            grid:  { color: "rgba(255,255,255,0.06)" },
          },
          y: {
            type: "category",
            labels: ACTUATOR_ORDER,
            ticks: { color: "#9a9a9a", font: { size: 11 } },
            grid:  { color: "rgba(255,255,255,0.06)" },
          },
        },
      },
    });
  } catch (e) { console.warn("Timeline chart failed:", e); }
}

async function loadHvacRuntimeChart(range) {
  try {
    const res  = await fetch(`/api/hvac_runtime?range=${range}`);
    const data = await res.json();

    const ctx = document.getElementById("hvac-runtime-chart");
    if (!ctx || !data.length) return;
    if (_hvacRuntimeChart) _hvacRuntimeChart.destroy();
    _hvacRuntimeChart = new Chart(ctx, {
      type: "bar",
      data: {
        datasets: [{
          label: "HVAC hours",
          data:  data.map(r => ({ x: new Date(r.day), y: r.hours })),
          backgroundColor: "#f0a030",
          borderWidth: 0,
          borderRadius: 3,
        }],
      },
      options: chartOptions(range, "hours"),
    });
  } catch (e) { console.warn("HVAC runtime chart failed:", e); }
}

async function loadDiagPowerChart() {
  const range = document.getElementById("diag-power-range")?.value || "24h";
  const data  = await fetchPower(range, _diagPowerOffset);
  updateDateLabel("diag-power-date-label", data);
  if (!data.length) return;

  const phaseA = data.map(r => ({ x: new Date(r.timestamp), y: r.power_a_kw     }));
  const phaseB = data.map(r => ({ x: new Date(r.timestamp), y: r.power_b_kw     }));
  const total  = data.map(r => ({ x: new Date(r.timestamp), y: r.power_total_kw }));

  const ctx = document.getElementById("diag-power-chart");
  if (!ctx) return;
  if (_diagPowerChart) _diagPowerChart.destroy();
  _diagPowerChart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        { label: "Phase A kW", data: phaseA, borderColor: "#ce93d8", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
        { label: "Phase B kW", data: phaseB, borderColor: "#f0a030", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
        { label: "Total kW",   data: total,  borderColor: "#4fc3f7", borderWidth: 2, pointRadius: 0, tension: 0, fill: false },
      ],
    },
    options: chartOptions(range, "kW"),
  });
}

function shiftDiagPowerRange(dir) { _diagPowerOffset += dir; loadDiagPowerChart(); }

// ---------------------------------------------------------------------------
// Shared Chart.js options
// ---------------------------------------------------------------------------
function timeScaleConfig(range) {
  switch (range) {
    case "1h":  return { unit: "minute", stepSize: 5,  displayFormats: { minute: "HH:mm" } };
    case "24h": return { unit: "hour",   stepSize: 3,  displayFormats: { hour: "HH:mm",  day: "MM/dd" } };
    case "7d":  return { unit: "day",    stepSize: 1,  displayFormats: { day: "MM/dd" } };
    case "30d": return { unit: "day",    stepSize: 5,  displayFormats: { day: "MM/dd" } };
    case "1y":  return { unit: "month",  stepSize: 1,  displayFormats: { month: "MMM"  } };
    default:    return { unit: "hour",   stepSize: 1,  displayFormats: { hour: "HH:mm",  day: "MM/dd" } };
  }
}

function chartOptions(range, yLabel) {
  const ts = timeScaleConfig(range);
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: {
        labels: {
          color: "#9a9a9a", font: { size: 11 }, usePointStyle: false, boxWidth: 18, boxHeight: 10,
          generateLabels(chart) {
            const labels = Chart.defaults.plugins.legend.labels.generateLabels(chart);
            labels.forEach(l => { l.fillStyle = l.strokeStyle; l.lineWidth = 0; });
            return labels;
          },
        },
      },
      tooltip: {
        backgroundColor: "#1e1e1e", borderColor: "#4a4a4a", borderWidth: 1,
        titleColor: "#9a9a9a", bodyColor: "#ffffff",
      },
    },
    scales: {
      x: {
        type: "time",
        time: { unit: ts.unit, stepSize: ts.stepSize, displayFormats: ts.displayFormats },
        ticks: { color: "#9a9a9a", maxRotation: 0, font: { size: 11 } },
        grid:  { color: "rgba(255,255,255,0.06)" },
      },
      y: {
        ticks: { color: "#9a9a9a", font: { size: 11 } },
        grid:  { color: "rgba(255,255,255,0.06)" },
        title: yLabel ? { display: true, text: yLabel, color: "#9a9a9a", font: { size: 11 } } : undefined,
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

function fmtTemp(f) { return Math.round(f) + "°F"; }
function celsiusToF(c) { return c * 9 / 5 + 32; }
function avg(arr) { return arr.reduce((a, b) => a + b, 0) / arr.length; }

function fmtShortTime(d) {
  if (!(d instanceof Date)) d = new Date(d);
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatRelTime(ts) {
  try {
    const d    = new Date(ts);
    const mins = Math.round((Date.now() - d) / 60000);
    if (mins < 2)  return "just now";
    if (mins < 60) return `${mins}m ago`;
    return `${Math.round(mins / 60)}h ago`;
  } catch { return ts; }
}

function updateDateLabel(id, data) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!data || !data.length) { el.textContent = "no data"; return; }
  try {
    const first = new Date(data[0].timestamp);
    const last  = new Date(data[data.length - 1].timestamp);
    const fmt   = { month: "2-digit", day: "2-digit" };
    el.textContent = first.toLocaleDateString("en-US", fmt) + " – " + last.toLocaleDateString("en-US", fmt);
  } catch { el.textContent = "—"; }
}
