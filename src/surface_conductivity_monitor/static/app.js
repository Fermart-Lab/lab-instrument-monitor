const metersRoot = document.getElementById("meters");
const meta = document.getElementById("meta");
const template = document.getElementById("meter-card-template");
const powerName = document.getElementById("power-name");
const powerError = document.getElementById("power-error");
const powerMeasuredVoltage = document.getElementById("power-measured-voltage");
const powerMeasuredCurrent = document.getElementById("power-measured-current");
const powerSetVoltage = document.getElementById("power-set-voltage");
const powerSetCurrent = document.getElementById("power-set-current");
const powerToggle = document.getElementById("power-toggle");
const setVoltageForm = document.getElementById("set-voltage-form");
const setCurrentForm = document.getElementById("set-current-form");
const setVoltageInput = document.getElementById("set-voltage-input");
const setCurrentInput = document.getElementById("set-current-input");
const recordingPath = document.getElementById("recording-path");
const recordingChooseFile = document.getElementById("recording-choose-file");
const recordingToggle = document.getElementById("recording-toggle");
const recordingTimer = document.getElementById("recording-timer");
const recordingFileDialogNote = document.getElementById("recording-file-dialog-note");
const recordingWarning = document.getElementById("recording-warning");
const recordingError = document.getElementById("recording-error");

const defaultPowerLimits = {
  voltageMin: 0,
  voltageMax: 30,
  currentMin: 0,
  currentMax: 5,
};

const meterModeLabels = {
  voltage: "Voltage",
  current: "Current",
  resistance: "Resistance",
  temperature: "Temperature",
  frequency: "Frequency",
  capacitance: "Capacitance",
};

const meterModeUnits = {
  voltage: "V",
  current: "A",
  resistance: "Ohm",
  temperature: "C",
  frequency: "Hz",
  capacitance: "F",
};

const state = {
  cards: new Map(),
  lineColors: ["#0f8a76", "#d45f2a", "#2f66d0", "#a82fd0"],
  refreshInFlight: false,
  refreshTimer: null,
  refreshIntervalMs: 500,
  hiddenIntervalMs: 2000,
  forceRedraw: false,
  powerBusy: false,
  meterBusy: new Set(),
  recordingBusy: false,
  recordingActive: false,
  recordingSaving: false,
  recordingDirty: false,
  recordingFilePath: "",
  recordingOverwriteWarning: false,
  recordingOverwriteArmed: false,
  recordingUiError: "",
  meterControlsLocked: false,
};

function ensureCard(meterName, idx) {
  if (state.cards.has(meterName)) {
    return state.cards.get(meterName);
  }

  const fragment = template.content.cloneNode(true);
  const card = fragment.querySelector(".card");
  const nameEl = fragment.querySelector(".meter-name");
  const valueEl = fragment.querySelector(".reading-value");
  const labelEl = fragment.querySelector(".meter-reading-label");
  const modeEl = fragment.querySelector(".meter-mode-select");
  const errorEl = fragment.querySelector(".error");
  const canvas = fragment.querySelector(".chart");
  const ctx = canvas.getContext("2d");

  nameEl.textContent = meterName;
  modeEl.addEventListener("change", async () => {
    if (state.meterControlsLocked) {
      modeEl.disabled = true;
      return;
    }

    state.meterBusy.add(meterName);
    syncMeterCardState(meterName);
    try {
      await postJson(`/api/meters/${encodeURIComponent(meterName)}/mode`, { mode: modeEl.value });
      await refresh();
    } catch (err) {
      errorEl.textContent = `Set mode failed: ${err.message || err}`;
    } finally {
      state.meterBusy.delete(meterName);
      syncMeterCardState(meterName);
    }
  });

  metersRoot.appendChild(fragment);

  const entry = {
    card,
    valueEl,
    labelEl,
    modeEl,
    errorEl,
    canvas,
    ctx,
    color: state.lineColors[idx % state.lineColors.length],
    chartKey: "",
  };
  state.cards.set(meterName, entry);
  return entry;
}

function syncMeterCardState(meterName) {
  const card = state.cards.get(meterName);
  if (!card) return;
  const locked = state.meterControlsLocked;
  card.modeEl.disabled = locked || state.meterBusy.has(meterName);
  card.card.classList.toggle("recording-locked", locked);
}

function drawChart(ctx, points, windowSeconds, color) {
  const width = ctx.canvas.width;
  const height = ctx.canvas.height;
  const pad = 30;

  ctx.clearRect(0, 0, width, height);

  ctx.strokeStyle = "#d6deea";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, pad / 2);
  ctx.lineTo(pad, height - pad);
  ctx.lineTo(width - 8, height - pad);
  ctx.stroke();

  if (!points || points.length < 2) {
    ctx.fillStyle = "#5f6c80";
    ctx.font = "14px sans-serif";
    ctx.fillText("Waiting for data...", pad + 8, height / 2);
    return;
  }

  let minY = points[0].y;
  let maxY = points[0].y;
  for (const p of points) {
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }

  if (minY === maxY) {
    minY -= 1;
    maxY += 1;
  }

  const yRange = maxY - minY;

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();

  points.forEach((p, index) => {
    const age = Math.min(windowSeconds, Math.max(0, p.x));
    const x = pad + ((windowSeconds - age) / windowSeconds) * (width - pad - 8);
    const y = height - pad - ((p.y - minY) / yRange) * (height - pad - pad / 2);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();

  ctx.fillStyle = "#5f6c80";
  ctx.font = "11px sans-serif";
  ctx.fillText("0s", width - 26, height - 10);
  ctx.fillText(`${windowSeconds}s`, pad - 12, height - 10);
  ctx.fillText(maxY.toFixed(6), 4, pad / 2 + 4);
  ctx.fillText(minY.toFixed(6), 4, height - pad);
}

function formatMetric(value, decimals, unit) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  return `${Number(value).toFixed(decimals)} ${unit}`;
}

function formatMeterValue(value, unit) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  return `${Number(value).toFixed(6)} ${unit}`;
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function setPowerControlsEnabled(enabled) {
  powerToggle.disabled = !enabled || state.powerBusy;
  setVoltageInput.disabled = !enabled || state.powerBusy;
  setCurrentInput.disabled = !enabled || state.powerBusy;
  setVoltageForm.querySelector("button").disabled = !enabled || state.powerBusy;
  setCurrentForm.querySelector("button").disabled = !enabled || state.powerBusy;
}

function renderPowerSource(powerSource) {
  if (!powerSource) {
    powerName.textContent = "Power Source";
    powerError.textContent = "Power source unavailable";
    powerMeasuredVoltage.textContent = "--";
    powerMeasuredCurrent.textContent = "--";
    powerSetVoltage.textContent = "--";
    powerSetCurrent.textContent = "--";
    powerToggle.textContent = "Output OFF";
    setPowerControlsEnabled(false);
    return;
  }

  const controlsEnabled = powerSource.enabled && !powerSource.error;
  const limits = powerSource.limits || {};
  const voltageMin = Number.isFinite(Number(limits.voltage_min))
    ? Number(limits.voltage_min)
    : defaultPowerLimits.voltageMin;
  const voltageMax = Number.isFinite(Number(limits.voltage_max))
    ? Number(limits.voltage_max)
    : defaultPowerLimits.voltageMax;
  const currentMin = Number.isFinite(Number(limits.current_min))
    ? Number(limits.current_min)
    : defaultPowerLimits.currentMin;
  const currentMax = Number.isFinite(Number(limits.current_max))
    ? Number(limits.current_max)
    : defaultPowerLimits.currentMax;

  powerName.textContent = powerSource.name || "Power Source";
  powerError.textContent = powerSource.error || "";
  powerMeasuredVoltage.textContent = formatMetric(powerSource.measured_voltage, 3, "V");
  powerMeasuredCurrent.textContent = formatMetric(powerSource.measured_current, 3, "A");
  powerSetVoltage.textContent = formatMetric(powerSource.set_voltage, 3, "V");
  powerSetCurrent.textContent = formatMetric(powerSource.set_current, 3, "A");
  const outputEnabled = Boolean(powerSource.output_enabled);
  powerToggle.textContent = outputEnabled ? "Output ON" : "Output OFF";
  powerToggle.dataset.enabled = outputEnabled ? "1" : "0";
  setVoltageInput.min = `${voltageMin}`;
  setVoltageInput.max = `${voltageMax}`;
  setCurrentInput.min = `${currentMin}`;
  setCurrentInput.max = `${currentMax}`;
  setPowerControlsEnabled(controlsEnabled);
}

function renderRecording(recording) {
  const info = recording || {};
  if (Object.prototype.hasOwnProperty.call(info, "active")) {
    state.recordingActive = Boolean(info.active);
  }
  if (Object.prototype.hasOwnProperty.call(info, "saving")) {
    state.recordingSaving = Boolean(info.saving);
  }
  if (Object.prototype.hasOwnProperty.call(info, "file_path")) {
    state.recordingFilePath = info.file_path || "";
  }
  if (Object.prototype.hasOwnProperty.call(info, "overwrite_warning")) {
    state.recordingOverwriteWarning = Boolean(info.overwrite_warning);
  }
  if (!state.recordingOverwriteWarning) {
    state.recordingOverwriteArmed = false;
  }
  if (!state.recordingDirty || document.activeElement !== recordingPath) {
    recordingPath.value = state.recordingFilePath;
  }
  recordingTimer.textContent = formatDuration(info.elapsed_seconds || 0);
  if (state.recordingSaving) {
    recordingToggle.textContent = "Saving...";
  } else if (state.recordingActive) {
    recordingToggle.textContent = "Stop Recording";
  } else if (state.recordingOverwriteArmed) {
    recordingToggle.textContent = "Confirm Overwrite";
  } else {
    recordingToggle.textContent = "Start Recording";
  }
  recordingToggle.dataset.active = state.recordingActive ? "1" : "0";
  recordingToggle.disabled = state.recordingBusy || state.recordingSaving || (!state.recordingActive && !state.recordingFilePath);
  recordingPath.disabled = state.recordingBusy || state.recordingActive || state.recordingSaving;
  recordingChooseFile.disabled = state.recordingBusy || state.recordingActive || state.recordingSaving || info.file_dialog_available === false;
  recordingFileDialogNote.textContent = info.file_dialog_available === false ? (info.file_dialog_reason || "") : "";
  if (state.recordingSaving) {
    recordingWarning.textContent = "Saving recording to file...";
  } else if (state.recordingOverwriteArmed && !state.recordingActive) {
    recordingWarning.textContent = "Selected file exists. Click Confirm Overwrite to start recording with this file.";
  } else if (state.recordingOverwriteWarning && !state.recordingActive) {
    recordingWarning.textContent = "Selected file already exists. Starting recording will overwrite it.";
  } else {
    recordingWarning.textContent = "";
  }
  recordingError.textContent = info.error || state.recordingUiError || "";
}

function applyRecordingSelection(recording) {
  state.recordingDirty = false;
  state.recordingFilePath = recording?.file_path || "";
  state.recordingOverwriteWarning = Boolean(recording?.overwrite_warning);
  state.recordingOverwriteArmed = false;
  state.recordingSaving = Boolean(recording?.saving);
  state.recordingUiError = "";
  recordingPath.value = state.recordingFilePath;
  renderRecording(recording);
}

async function saveRecordingPath() {
  const path = recordingPath.value.trim();
  if (!path) {
    state.recordingDirty = false;
    state.recordingOverwriteArmed = false;
    return;
  }
  state.recordingBusy = true;
  state.recordingUiError = "";
  recordingError.textContent = "";
  try {
    const recording = await postJson("/api/recording/path", { path });
    applyRecordingSelection(recording);
    return true;
  } catch (err) {
    state.recordingUiError = err.message || String(err);
    recordingError.textContent = state.recordingUiError;
    return false;
  } finally {
    state.recordingBusy = false;
    await refresh();
  }
}

function applyLockState(locked) {
  state.meterControlsLocked = Boolean(locked);
  for (const meterName of state.cards.keys()) {
    syncMeterCardState(meterName);
  }
}

async function postJson(url, body = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload && payload.detail) message = payload.detail;
    } catch (_err) {
      // Use default message when body is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

async function syncPowerFromLatestState() {
  try {
    const latest = await fetch("/api/state", { cache: "no-store" });
    if (!latest.ok) {
      setPowerControlsEnabled(false);
      return;
    }
    const payload = await latest.json();
    renderPowerSource(payload.power_source);
    renderRecording(payload.recording);
    applyLockState(payload.meter_controls_locked);
  } catch (_err) {
    setPowerControlsEnabled(false);
  }
}

async function refresh() {
  if (state.refreshInFlight) return;
  state.refreshInFlight = true;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const payload = await response.json();
    state.refreshIntervalMs = Math.max(250, Math.round(payload.poll_interval_seconds * 1000));
    const acquisitionInterval = Number(payload.acquisition_interval_seconds);
    const acquisitionText = Number.isFinite(acquisitionInterval) ? acquisitionInterval.toFixed(2) : "--";
    meta.textContent = `Window: ${payload.window_seconds}s | Screen: ${payload.poll_interval_seconds}s | Logging: ${acquisitionText}s | Updated: ${new Date(payload.server_time * 1000).toLocaleTimeString()}`;
    renderPowerSource(payload.power_source);
    renderRecording(payload.recording);
    applyLockState(payload.meter_controls_locked);

    payload.meters.forEach((meter, idx) => {
      const card = ensureCard(meter.name, idx);
      const mode = meter.mode || "voltage";
      const unit = meter.unit || meterModeUnits[mode] || "";
      const supportedModes = Array.isArray(meter.supported_modes) ? meter.supported_modes : Object.keys(meterModeLabels);
      for (const option of card.modeEl.options) {
        option.hidden = !supportedModes.includes(option.value);
      }
      card.modeEl.value = supportedModes.includes(mode) ? mode : supportedModes[0];
      card.labelEl.textContent = meterModeLabels[mode] || "Reading";
      card.valueEl.textContent = formatMeterValue(meter.value, unit);
      card.errorEl.textContent = meter.error || "";
      syncMeterCardState(meter.name);
      const history = meter.history || [];
      const last = history.length > 0 ? history[history.length - 1] : null;
      const chartKey = `${mode}|${payload.window_seconds}|${history.length}|${last ? `${last.t}|${last.y}` : "empty"}`;
      if (state.forceRedraw || card.chartKey !== chartKey) {
        drawChart(card.ctx, history, payload.window_seconds, card.color);
        card.chartKey = chartKey;
      }
    });
  } catch (err) {
    meta.textContent = `Connection error: ${err}`;
  } finally {
    state.forceRedraw = false;
    state.refreshInFlight = false;
    if (state.refreshTimer !== null) {
      clearTimeout(state.refreshTimer);
    }
    const delay = document.hidden
      ? Math.max(state.hiddenIntervalMs, state.refreshIntervalMs)
      : state.refreshIntervalMs;
    state.refreshTimer = setTimeout(refresh, delay);
  }
}

recordingChooseFile.addEventListener("click", async () => {
  state.recordingBusy = true;
  state.recordingDirty = false;
  state.recordingUiError = "";
  renderRecording({
    active: state.recordingActive,
    file_path: recordingPath.value,
    overwrite_warning: state.recordingOverwriteWarning,
  });
  recordingError.textContent = "";
  try {
    const recording = await postJson("/api/recording/choose-file", { current_path: recordingPath.value });
    applyRecordingSelection(recording);
  } catch (err) {
    state.recordingUiError = err.message || String(err);
    recordingError.textContent = state.recordingUiError;
  } finally {
    state.recordingBusy = false;
    await refresh();
  }
});

recordingToggle.addEventListener("click", async () => {
  if (!state.recordingActive && state.recordingDirty) {
    const saved = await saveRecordingPath();
    if (!saved) {
      return;
    }
  }
  state.recordingBusy = true;
  state.recordingUiError = "";
  recordingError.textContent = "";
  renderRecording({
    active: state.recordingActive,
    file_path: state.recordingFilePath || recordingPath.value,
    overwrite_warning: state.recordingOverwriteWarning,
  });
  try {
    if (state.recordingActive) {
      const recording = await postJson("/api/recording/stop");
      applyRecordingSelection(recording);
    } else {
      if (state.recordingOverwriteWarning && !state.recordingOverwriteArmed) {
        state.recordingOverwriteArmed = true;
        state.recordingUiError = "";
        recordingError.textContent = "";
        return;
      }
      const overwrite = state.recordingOverwriteWarning;
      const recording = await postJson("/api/recording/start", { overwrite });
      applyRecordingSelection(recording);
    }
  } catch (err) {
    state.recordingUiError = err.message || String(err);
    recordingError.textContent = state.recordingUiError;
  } finally {
    state.recordingBusy = false;
    await refresh();
  }
});

recordingPath.addEventListener("input", () => {
  state.recordingDirty = true;
  state.recordingOverwriteArmed = false;
});

recordingPath.addEventListener("change", async () => {
  await saveRecordingPath();
});

recordingPath.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") {
    return;
  }
  event.preventDefault();
  await saveRecordingPath();
});

recordingPath.addEventListener("blur", async () => {
  await saveRecordingPath();
});

setVoltageForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = Number(setVoltageInput.value);
  const min = Number(setVoltageInput.min || defaultPowerLimits.voltageMin);
  const max = Number(setVoltageInput.max || defaultPowerLimits.voltageMax);
  if (!Number.isFinite(value) || value < min || value > max) {
    powerError.textContent = `Voltage off limits: allowed range is ${min} to ${max} V`;
    return;
  }
  state.powerBusy = true;
  setPowerControlsEnabled(true);
  try {
    const power = await postJson("/api/power-source/voltage", { value });
    renderPowerSource(power);
  } catch (err) {
    powerError.textContent = `Set voltage failed: ${err.message || err}`;
  } finally {
    state.powerBusy = false;
    await syncPowerFromLatestState();
  }
});

setCurrentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = Number(setCurrentInput.value);
  const min = Number(setCurrentInput.min || defaultPowerLimits.currentMin);
  const max = Number(setCurrentInput.max || defaultPowerLimits.currentMax);
  if (!Number.isFinite(value) || value < min || value > max) {
    powerError.textContent = `Current off limits: allowed range is ${min} to ${max} A`;
    return;
  }
  state.powerBusy = true;
  setPowerControlsEnabled(true);
  try {
    const power = await postJson("/api/power-source/current", { value });
    renderPowerSource(power);
  } catch (err) {
    powerError.textContent = `Set current failed: ${err.message || err}`;
  } finally {
    state.powerBusy = false;
    await syncPowerFromLatestState();
  }
});

powerToggle.addEventListener("click", async () => {
  const enabled = powerToggle.dataset.enabled !== "1";
  state.powerBusy = true;
  setPowerControlsEnabled(true);
  try {
    const power = await postJson("/api/power-source/output", { enabled });
    renderPowerSource(power);
  } catch (err) {
    powerError.textContent = `Set output failed: ${err.message || err}`;
  } finally {
    state.powerBusy = false;
    await syncPowerFromLatestState();
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    state.forceRedraw = true;
    if (state.refreshTimer !== null) {
      clearTimeout(state.refreshTimer);
      state.refreshTimer = null;
    }
    refresh();
  }
});

refresh();
