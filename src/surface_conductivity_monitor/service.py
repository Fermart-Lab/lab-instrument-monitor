from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

from .config import AppConfig, MeasurementMode, SUPPORTED_MEASUREMENT_MODES
from .meters import (
    MeterDevice,
    MeasurementReading,
    MeterReadError,
    PowerSourceDevice,
    PowerSourceError,
    build_meter,
    build_power_source,
)
from .recording import (
    RecordingColumn,
    RecordingSpool,
    RecordingTarget,
    append_recording_rows,
    canonical_unit_for_mode,
    cleanup_recording_spool,
    choose_recording_path,
    finalize_recording_spool,
    label_for_mode,
    parse_recording_target,
    prepare_recording_spool,
    recording_file_dialog_available,
)


@dataclass
class MeterRuntime:
    name: str
    device: Optional[MeterDevice]
    mode: MeasurementMode
    value: Optional[float]
    display_unit: str
    error: Optional[str]
    history: Deque[Tuple[float, float]]
    mode_sync_interval_seconds: float
    last_mode_sync_monotonic: float
    consecutive_failures: int
    next_poll_monotonic: float


@dataclass
class PowerSourceRuntime:
    name: str
    enabled: bool
    device: Optional[PowerSourceDevice]
    measured_voltage: Optional[float]
    measured_current: Optional[float]
    set_voltage: Optional[float]
    set_current: Optional[float]
    output_enabled: Optional[bool]
    error: Optional[str]
    consecutive_failures: int
    next_poll_monotonic: float


@dataclass
class RecordingRuntime:
    target: Optional[RecordingTarget]
    spool: Optional[RecordingSpool]
    active: bool
    saving: bool
    started_at: Optional[float]
    columns: List[RecordingColumn]
    pending_rows: List[List[object]]
    last_flush_monotonic: float
    error: Optional[str]


class MeterMonitorService:
    MAX_POWER_SOURCE_VOLTAGE = 30.0
    MAX_POWER_SOURCE_CURRENT = 5.0
    METER_UNITS = {
        "voltage": "V",
        "current": "A",
        "resistance": "Ohm",
        "temperature": "C",
        "frequency": "Hz",
        "capacitance": "F",
    }
    RECORDING_FLUSH_ROW_COUNT = 20
    RECORDING_FLUSH_INTERVAL_SECONDS = 1.0
    DEVICE_RETRY_INITIAL_BACKOFF_SECONDS = 1.0
    DEVICE_RETRY_MAX_BACKOFF_SECONDS = 5.0

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._meters: Dict[str, MeterRuntime] = {}
        self._power_source: Optional[PowerSourceRuntime] = None
        self._power_source_lock = asyncio.Lock()
        self._recording_lock = asyncio.Lock()
        self._recording = RecordingRuntime(
            target=None,
            spool=None,
            active=False,
            saving=False,
            started_at=None,
            columns=[],
            pending_rows=[],
            last_flush_monotonic=0.0,
            error=None,
        )
        self._recording_save_task: Optional[asyncio.Task[None]] = None
        available, reason = recording_file_dialog_available()
        self._recording_file_dialog_available = available
        self._recording_file_dialog_reason = reason
        self._recording_task: Optional[asyncio.Task[None]] = None
        self._device_tasks: List[asyncio.Task[None]] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return

        self._meters = {}
        for meter_conf in [self._config.meter_1, self._config.meter_2]:
            try:
                device = build_meter(meter_conf)
                error = None
            except Exception as exc:
                device = None
                error = f"Startup connection error: {exc}"

            self._meters[meter_conf.name] = MeterRuntime(
                name=meter_conf.name,
                device=device,
                mode=meter_conf.initial_mode,
                value=None,
                display_unit=self.METER_UNITS[meter_conf.initial_mode],
                error=error,
                history=deque(),
                mode_sync_interval_seconds=max(0.0, meter_conf.mode_sync_interval_seconds),
                last_mode_sync_monotonic=0.0,
                consecutive_failures=0,
                next_poll_monotonic=0.0,
            )
            if device is not None:
                try:
                    device.set_mode(meter_conf.initial_mode)
                except Exception as exc:
                    runtime = self._meters[meter_conf.name]
                    runtime.error = f"Startup mode error: {exc}"

        power_conf = self._config.power_source
        if power_conf.enabled:
            try:
                power_device = build_power_source(power_conf)
                power_error = None
            except Exception as exc:
                power_device = None
                power_error = f"Startup connection error: {exc}"
            self._power_source = PowerSourceRuntime(
                name=power_conf.name,
                enabled=True,
                device=power_device,
                measured_voltage=None,
                measured_current=None,
                set_voltage=None,
                set_current=None,
                output_enabled=None,
                error=power_error,
                consecutive_failures=0,
                next_poll_monotonic=0.0,
            )
        else:
            self._power_source = PowerSourceRuntime(
                name=power_conf.name,
                enabled=False,
                device=None,
                measured_voltage=None,
                measured_current=None,
                set_voltage=None,
                set_current=None,
                output_enabled=None,
                error="Power source disabled by configuration",
                consecutive_failures=0,
                next_poll_monotonic=0.0,
            )

        self._running = True
        interval = max(0.05, self._config.acquisition_interval_seconds)
        window = self._config.window_seconds
        self._device_tasks = [
            asyncio.create_task(self._meter_loop(runtime, interval, window))
            for runtime in self._meters.values()
            if runtime.device is not None
        ]
        if self._power_source and self._power_source.enabled and self._power_source.device is not None:
            self._device_tasks.append(asyncio.create_task(self._power_source_loop(interval)))
        self._recording_task = asyncio.create_task(self._recording_loop(interval))

    async def stop(self) -> None:
        self._running = False
        if self._recording_task:
            await self._recording_task
        if self._device_tasks:
            await asyncio.gather(*self._device_tasks, return_exceptions=True)
            self._device_tasks = []
        if self._recording_save_task:
            await self._recording_save_task
        cleanup_recording_spool(self._recording.spool)
        for runtime in self._meters.values():
            if runtime.device is not None:
                try:
                    runtime.device.close()
                except Exception:
                    pass
        if self._power_source and self._power_source.device is not None:
            try:
                self._power_source.device.close()
            except Exception:
                pass

    async def _meter_loop(
        self,
        runtime: MeterRuntime,
        interval: float,
        window: float,
    ) -> None:
        next_tick = time.monotonic()

        while self._running:
            ts = time.time()
            await self._poll_meter(runtime, ts, window)
            next_tick += interval
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))

    async def _power_source_loop(self, interval: float) -> None:
        next_tick = time.monotonic()

        while self._running:
            await self._poll_power_source()
            next_tick += interval
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))

    async def _recording_loop(self, interval: float) -> None:
        next_tick = time.monotonic()

        while self._running:
            if self._recording.active:
                async with self._recording_lock:
                    if self._recording.active:
                        self._recording.pending_rows.append(self._build_recording_row(time.time()))
                        await self._flush_recording_rows_if_needed(force=False)
            next_tick += interval
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))

    async def _poll_meter(
        self,
        runtime: MeterRuntime,
        ts: float,
        window: float,
    ) -> None:
        if runtime.device is None:
            return
        now_mono = time.monotonic()
        if runtime.next_poll_monotonic > now_mono:
            self._trim_history(runtime, ts, window)
            return
        try:
            reading, actual_mode, mode_checked_at = await asyncio.to_thread(
                self._read_meter_once,
                runtime,
            )
            if actual_mode is not None and actual_mode != runtime.mode:
                runtime.mode = actual_mode
                runtime.value = None
                runtime.display_unit = self.METER_UNITS[actual_mode]
                runtime.history.clear()
            if mode_checked_at is not None:
                runtime.last_mode_sync_monotonic = mode_checked_at
            runtime.value = reading.value
            runtime.display_unit = reading.display_unit
            runtime.consecutive_failures = 0
            runtime.next_poll_monotonic = 0.0
            runtime.error = None
            runtime.history.append((ts, reading.value))
        except MeterReadError as exc:
            runtime.consecutive_failures += 1
            runtime.next_poll_monotonic = now_mono + self._device_retry_backoff_seconds(
                runtime.consecutive_failures
            )
            if runtime.consecutive_failures >= 2:
                runtime.error = str(exc)
        except Exception as exc:  # pragma: no cover
            runtime.consecutive_failures += 1
            runtime.next_poll_monotonic = now_mono + self._device_retry_backoff_seconds(
                runtime.consecutive_failures
            )
            if runtime.consecutive_failures >= 2:
                runtime.error = f"Unexpected read error: {exc}"

        self._trim_history(runtime, ts, window)

    async def _poll_power_source(self) -> None:
        if self._power_source is None or not self._power_source.enabled or self._power_source.device is None:
            return
        now_mono = time.monotonic()
        if self._power_source.next_poll_monotonic > now_mono:
            return
        async with self._power_source_lock:
            await asyncio.to_thread(self._refresh_power_source, self._power_source)

    async def set_meter_mode(self, meter_name: str, mode: MeasurementMode) -> Dict[str, object]:
        if self._recording.active:
            raise MeterReadError("Meter mode changes are disabled while recording")
        runtime = self._meters.get(meter_name)
        if runtime is None:
            raise MeterReadError(f"Unknown meter '{meter_name}'")

        if runtime.device is not None:
            try:
                await asyncio.to_thread(runtime.device.set_mode, mode)
            except Exception as exc:
                raise MeterReadError(f"{meter_name}: failed to switch mode: {exc}") from exc
            runtime.mode = mode
            runtime.last_mode_sync_monotonic = time.monotonic()
        else:
            runtime.mode = mode

        runtime.value = None
        runtime.display_unit = self.METER_UNITS[mode]
        runtime.consecutive_failures = 0
        runtime.error = None if runtime.device is not None else runtime.error
        runtime.history.clear()
        return self.snapshot()

    def _refresh_power_source(self, runtime: PowerSourceRuntime) -> None:
        if runtime.device is None:
            return
        now_mono = time.monotonic()
        try:
            runtime.measured_voltage = runtime.device.read_output_voltage()
            runtime.measured_current = runtime.device.read_output_current()
            runtime.set_voltage = runtime.device.read_set_voltage()
            runtime.set_current = runtime.device.read_set_current()
            runtime.output_enabled = runtime.device.is_output_enabled()
            runtime.consecutive_failures = 0
            runtime.next_poll_monotonic = 0.0
            runtime.error = None
        except PowerSourceError as exc:
            runtime.consecutive_failures += 1
            runtime.next_poll_monotonic = now_mono + self._device_retry_backoff_seconds(
                runtime.consecutive_failures
            )
            runtime.error = str(exc)
        except Exception as exc:  # pragma: no cover
            runtime.consecutive_failures += 1
            runtime.next_poll_monotonic = now_mono + self._device_retry_backoff_seconds(
                runtime.consecutive_failures
            )
            runtime.error = f"Unexpected power source error: {exc}"

    def _read_meter_once(
        self,
        runtime: MeterRuntime,
    ) -> Tuple[MeasurementReading, Optional[MeasurementMode], Optional[float]]:
        actual_mode: Optional[MeasurementMode] = None
        mode_checked_at: Optional[float] = None
        now_mono = time.monotonic()
        if now_mono - runtime.last_mode_sync_monotonic >= runtime.mode_sync_interval_seconds:
            assert runtime.device is not None
            actual_mode = runtime.device.read_mode()
            mode_checked_at = now_mono
        active_mode = actual_mode or runtime.mode
        assert runtime.device is not None
        reading = runtime.device.read_measurement(active_mode)
        return reading, actual_mode, mode_checked_at

    def _trim_history(self, runtime: MeterRuntime, ts: float, window: float) -> None:
        cutoff = ts - window
        while runtime.history and runtime.history[0][0] < cutoff:
            runtime.history.popleft()

    def _device_retry_backoff_seconds(self, consecutive_failures: int) -> float:
        failures = max(1, consecutive_failures)
        return min(
            self.DEVICE_RETRY_MAX_BACKOFF_SECONDS,
            self.DEVICE_RETRY_INITIAL_BACKOFF_SECONDS * (2 ** (failures - 1)),
        )

    async def set_power_source_voltage(self, value: float) -> Dict[str, object]:
        runtime = self._require_power_source()
        if value < 0:
            raise PowerSourceError("Voltage must be >= 0")
        if value > self.MAX_POWER_SOURCE_VOLTAGE:
            raise PowerSourceError(
                f"Voltage off limits: max is {self.MAX_POWER_SOURCE_VOLTAGE:.3f} V"
            )
        async with self._power_source_lock:
            assert runtime.device is not None
            try:
                await asyncio.to_thread(runtime.device.set_voltage, value)
            except Exception as exc:
                raise PowerSourceError(f"Failed to set voltage: {exc}") from exc
            await asyncio.to_thread(self._refresh_power_source, runtime)
            if runtime.error:
                raise PowerSourceError(runtime.error)
            return self._power_source_snapshot()

    async def set_power_source_current(self, value: float) -> Dict[str, object]:
        runtime = self._require_power_source()
        if value < 0:
            raise PowerSourceError("Current must be >= 0")
        if value > self.MAX_POWER_SOURCE_CURRENT:
            raise PowerSourceError(
                f"Current off limits: max is {self.MAX_POWER_SOURCE_CURRENT:.3f} A"
            )
        async with self._power_source_lock:
            assert runtime.device is not None
            try:
                await asyncio.to_thread(runtime.device.set_current, value)
            except Exception as exc:
                raise PowerSourceError(f"Failed to set current: {exc}") from exc
            await asyncio.to_thread(self._refresh_power_source, runtime)
            if runtime.error:
                raise PowerSourceError(runtime.error)
            return self._power_source_snapshot()

    async def set_power_source_output_enabled(self, enabled: bool) -> Dict[str, object]:
        runtime = self._require_power_source()
        async with self._power_source_lock:
            assert runtime.device is not None
            try:
                await asyncio.to_thread(runtime.device.set_output_enabled, enabled)
            except Exception as exc:
                raise PowerSourceError(f"Failed to set output state: {exc}") from exc
            await asyncio.to_thread(self._refresh_power_source, runtime)
            if runtime.error:
                raise PowerSourceError(runtime.error)
            return self._power_source_snapshot()

    def _require_power_source(self) -> PowerSourceRuntime:
        runtime = self._power_source
        if runtime is None or not runtime.enabled:
            raise PowerSourceError("Power source is disabled")
        if runtime.device is None:
            detail = runtime.error or "Power source is not connected"
            raise PowerSourceError(detail)
        return runtime

    def _power_source_snapshot(self) -> Dict[str, object]:
        runtime = self._power_source
        if runtime is None:
            return {"enabled": False, "name": "Power Source", "error": "Power source not configured"}
        return {
            "name": runtime.name,
            "enabled": runtime.enabled,
            "measured_voltage": runtime.measured_voltage,
            "measured_current": runtime.measured_current,
            "set_voltage": runtime.set_voltage,
            "set_current": runtime.set_current,
            "output_enabled": runtime.output_enabled,
            "error": runtime.error,
            "limits": {
                "voltage_min": 0.0,
                "voltage_max": self.MAX_POWER_SOURCE_VOLTAGE,
                "current_min": 0.0,
                "current_max": self.MAX_POWER_SOURCE_CURRENT,
            },
        }

    async def choose_recording_target(self, current_path: Optional[str]) -> Dict[str, object]:
        if self._recording.active:
            raise RuntimeError("Stop the current recording before choosing another file")
        if not self._recording_file_dialog_available:
            raise RuntimeError(self._recording_file_dialog_reason or "Desktop file chooser is unavailable")
        target = await asyncio.to_thread(choose_recording_path, current_path)
        async with self._recording_lock:
            self._recording.target = target
            self._recording.error = None
        return self.snapshot()["recording"]

    async def set_recording_target(self, raw_path: str) -> Dict[str, object]:
        if self._recording.active:
            raise RuntimeError("Stop the current recording before changing the file path")
        target = parse_recording_target(raw_path)
        async with self._recording_lock:
            self._recording.target = target
            self._recording.error = None
        return self.snapshot()["recording"]

    async def start_recording(self, overwrite: bool) -> Dict[str, object]:
        async with self._recording_lock:
            target = self._recording.target
            if target is None:
                raise RuntimeError("Choose a CSV or Excel file before starting recording")
            if self._recording.saving:
                raise RuntimeError("Wait for the current file save to finish before starting a new recording")
            if target.exists and not overwrite:
                raise RuntimeError("Selected file already exists and would be overwritten")

            self._recording.columns = self._build_recording_columns()
            headers = ["Time (s)", *[column.heading for column in self._recording.columns]]
            self._recording.spool = await asyncio.to_thread(prepare_recording_spool, target, headers)
            self._recording.pending_rows = []
            self._recording.last_flush_monotonic = time.monotonic()
            self._recording.started_at = time.time()
            self._recording.active = True
            self._recording.saving = False
            self._recording.error = None
        return self.snapshot()["recording"]

    async def stop_recording(self) -> Dict[str, object]:
        async with self._recording_lock:
            if not self._recording.active:
                return self.snapshot()["recording"]
            await self._flush_recording_rows_if_needed(force=True)
            spool = self._recording.spool
            assert spool is not None
            self._recording.active = False
            self._recording.saving = True
            self._recording.started_at = None
            self._recording.pending_rows = []
            self._recording.error = None
            self._recording_save_task = asyncio.create_task(
                self._finalize_recording_save(spool)
            )
        return self.snapshot()["recording"]

    async def _finalize_recording_save(
        self,
        spool: RecordingSpool,
    ) -> None:
        error: Optional[str] = None
        try:
            await asyncio.to_thread(finalize_recording_spool, spool)
        except Exception as exc:
            error = f"Failed to save recording: {exc}"
        async with self._recording_lock:
            self._recording.saving = False
            self._recording.error = error
            self._recording.spool = None
            self._recording_save_task = None

    async def _flush_recording_rows_if_needed(self, force: bool) -> None:
        spool = self._recording.spool
        if spool is None or not self._recording.pending_rows:
            return
        now_mono = time.monotonic()
        should_flush = force
        should_flush = should_flush or len(self._recording.pending_rows) >= self.RECORDING_FLUSH_ROW_COUNT
        should_flush = should_flush or (
            now_mono - self._recording.last_flush_monotonic >= self.RECORDING_FLUSH_INTERVAL_SECONDS
        )
        if not should_flush:
            return
        rows = list(self._recording.pending_rows)
        self._recording.pending_rows = []
        self._recording.last_flush_monotonic = now_mono
        await asyncio.to_thread(append_recording_rows, spool, rows)

    def _build_recording_columns(self) -> List[RecordingColumn]:
        columns: List[RecordingColumn] = []
        if self._power_source and self._power_source.enabled:
            columns.append(
                RecordingColumn(
                    key="power_source_voltage",
                    device_name=self._power_source.name,
                    quantity="voltage",
                    label="Voltage",
                    unit="V",
                )
            )
            columns.append(
                RecordingColumn(
                    key="power_source_current",
                    device_name=self._power_source.name,
                    quantity="current",
                    label="Current",
                    unit="A",
                )
            )
        for runtime in self._meters.values():
            columns.append(
                RecordingColumn(
                    key=f"meter:{runtime.name}",
                    device_name=runtime.name,
                    quantity=runtime.mode,
                    label=label_for_mode(runtime.mode),
                    unit=canonical_unit_for_mode(runtime.mode),
                )
            )
        return columns

    def _build_recording_row(self, sample_time: float) -> List[object]:
        started_at = self._recording.started_at or sample_time
        row: List[object] = [round(sample_time - started_at, 3)]
        meter_lookup = {runtime.name: runtime for runtime in self._meters.values()}

        for column in self._recording.columns:
            value: object
            if column.key == "power_source_voltage":
                runtime = self._power_source
                value = None if runtime is None else runtime.measured_voltage
            elif column.key == "power_source_current":
                runtime = self._power_source
                value = None if runtime is None else runtime.measured_current
            else:
                meter_name = column.key.split(":", 1)[1]
                meter_runtime = meter_lookup.get(meter_name)
                value = None if meter_runtime is None else meter_runtime.value
            row.append("" if value is None else value)
        return row

    def _recording_snapshot(self) -> Dict[str, object]:
        target = self._recording.target
        now = time.time()
        return {
            "active": self._recording.active,
            "saving": self._recording.saving,
            "file_path": None if target is None else str(target.path),
            "format": None if target is None else target.format,
            "file_exists": False if target is None else target.exists,
            "file_dialog_available": self._recording_file_dialog_available,
            "file_dialog_reason": self._recording_file_dialog_reason,
            "elapsed_seconds": (
                None
                if not self._recording.active or self._recording.started_at is None
                else round(now - self._recording.started_at, 1)
            ),
            "overwrite_warning": bool(target and target.exists),
            "error": self._recording.error,
            "columns": [column.heading for column in self._recording.columns],
        }

    def snapshot(self) -> Dict[str, object]:
        now = time.time()
        window = self._config.window_seconds
        meters: List[Dict[str, object]] = []
        for runtime in self._meters.values():
            self._trim_history(runtime, now, window)
            meters.append(
                {
                    "name": runtime.name,
                    "mode": runtime.mode,
                    "value": runtime.value,
                    "unit": runtime.display_unit,
                    "supported_modes": list(SUPPORTED_MEASUREMENT_MODES),
                    "error": runtime.error,
                    "history": [
                        {
                            "t": round(point_ts, 3),
                            "x": round(now - point_ts, 3),
                            "y": point_val,
                        }
                        for point_ts, point_val in runtime.history
                    ],
                }
            )

        return {
            "server_time": round(now, 3),
            "window_seconds": self._config.window_seconds,
            "poll_interval_seconds": self._config.ui_refresh_interval_seconds,
            "acquisition_interval_seconds": self._config.acquisition_interval_seconds,
            "meters": meters,
            "power_source": self._power_source_snapshot(),
            "recording": self._recording_snapshot(),
            "meter_controls_locked": self._recording.active,
        }
