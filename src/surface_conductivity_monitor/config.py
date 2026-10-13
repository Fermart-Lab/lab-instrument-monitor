from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args, **kwargs) -> None:
        return None

MeterDriver = Literal["serial", "visa"]
MeasurementMode = Literal["voltage", "current", "resistance", "temperature", "frequency", "capacitance"]
SUPPORTED_MEASUREMENT_MODES: tuple[MeasurementMode, ...] = (
    "voltage",
    "current",
    "resistance",
    "temperature",
    "frequency",
    "capacitance",
)


@dataclass(frozen=True)
class MeterConfig:
    name: str
    driver: MeterDriver
    identifier: str
    baudrate: int
    initial_mode: MeasurementMode
    mode_sync_interval_seconds: float
    voltage_read_command: str
    current_read_command: str
    resistance_read_command: str
    temperature_read_command: str
    frequency_read_command: str
    capacitance_read_command: str
    read_mode_command: str
    voltage_mode_tokens: tuple[str, ...]
    current_mode_tokens: tuple[str, ...]
    resistance_mode_tokens: tuple[str, ...]
    temperature_mode_tokens: tuple[str, ...]
    frequency_mode_tokens: tuple[str, ...]
    capacitance_mode_tokens: tuple[str, ...]
    voltage_set_mode_command: str
    current_set_mode_command: str
    resistance_set_mode_command: str
    temperature_set_mode_command: str
    frequency_set_mode_command: str
    capacitance_set_mode_command: str
    temperature_display_command: str
    temperature_unit_command: str

    def read_command_for_mode(self, mode: MeasurementMode) -> str:
        if mode == "voltage":
            return self.voltage_read_command
        if mode == "current":
            return self.current_read_command
        if mode == "resistance":
            return self.resistance_read_command
        if mode == "temperature":
            return self.temperature_read_command
        if mode == "frequency":
            return self.frequency_read_command
        return self.capacitance_read_command

    def set_mode_command_for_mode(self, mode: MeasurementMode) -> str:
        if mode == "voltage":
            return self.voltage_set_mode_command
        if mode == "current":
            return self.current_set_mode_command
        if mode == "resistance":
            return self.resistance_set_mode_command
        if mode == "temperature":
            return self.temperature_set_mode_command
        if mode == "frequency":
            return self.frequency_set_mode_command
        return self.capacitance_set_mode_command

    def mode_tokens_for_mode(self, mode: MeasurementMode) -> tuple[str, ...]:
        if mode == "voltage":
            return self.voltage_mode_tokens
        if mode == "current":
            return self.current_mode_tokens
        if mode == "resistance":
            return self.resistance_mode_tokens
        if mode == "temperature":
            return self.temperature_mode_tokens
        if mode == "frequency":
            return self.frequency_mode_tokens
        return self.capacitance_mode_tokens

    def post_set_commands_for_mode(self, mode: MeasurementMode) -> tuple[str, ...]:
        if mode != "temperature":
            return ()
        return tuple(
            command
            for command in (self.temperature_display_command, self.temperature_unit_command)
            if command
        )


@dataclass(frozen=True)
class PowerSourceConfig:
    name: str
    enabled: bool
    driver: MeterDriver
    identifier: str
    baudrate: int
    measure_voltage_command: str
    measure_current_command: str
    read_set_voltage_command: str
    read_set_current_command: str
    set_voltage_command: str
    set_current_command: str
    read_output_enabled_command: str
    set_output_enabled_command: str


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    acquisition_interval_seconds: float
    ui_refresh_interval_seconds: float
    window_seconds: int
    meter_1: MeterConfig
    meter_2: MeterConfig
    power_source: PowerSourceConfig


def _meter_from_env(prefix: str, default_name: str) -> MeterConfig:
    driver = os.getenv(f"{prefix}_DRIVER", "serial").strip().lower()
    if driver not in {"serial", "visa"}:
        raise ValueError(f"Invalid driver '{driver}' for {prefix}_DRIVER")
    legacy_read_command = os.getenv(f"{prefix}_READ_COMMAND", "READ?").strip() or "READ?"
    initial_mode = os.getenv(f"{prefix}_INITIAL_MODE", "voltage").strip().lower()
    if initial_mode not in SUPPORTED_MEASUREMENT_MODES:
        raise ValueError(f"Invalid mode '{initial_mode}' for {prefix}_INITIAL_MODE")

    return MeterConfig(
        name=os.getenv(f"{prefix}_NAME", default_name).strip() or default_name,
        driver=driver,
        identifier=os.getenv(f"{prefix}_IDENTIFIER", "").strip(),
        baudrate=int(os.getenv(f"{prefix}_BAUDRATE", "9600")),
        initial_mode=initial_mode,
        mode_sync_interval_seconds=float(os.getenv(f"{prefix}_MODE_SYNC_INTERVAL_SECONDS", "1.5")),
        voltage_read_command=os.getenv(f"{prefix}_VOLTAGE_READ_COMMAND", legacy_read_command).strip()
        or legacy_read_command,
        current_read_command=os.getenv(f"{prefix}_CURRENT_READ_COMMAND", legacy_read_command).strip()
        or legacy_read_command,
        resistance_read_command=os.getenv(f"{prefix}_RESISTANCE_READ_COMMAND", legacy_read_command).strip()
        or legacy_read_command,
        temperature_read_command=os.getenv(f"{prefix}_TEMPERATURE_READ_COMMAND", "MEAS:TEMP:RTD?").strip()
        or "MEAS:TEMP:RTD?",
        frequency_read_command=os.getenv(f"{prefix}_FREQUENCY_READ_COMMAND", "MEAS:FREQ?").strip()
        or "MEAS:FREQ?",
        capacitance_read_command=os.getenv(f"{prefix}_CAPACITANCE_READ_COMMAND", "MEAS:CAP?").strip()
        or "MEAS:CAP?",
        read_mode_command=os.getenv(f"{prefix}_READ_MODE_COMMAND", "").strip(),
        voltage_mode_tokens=_csv_tokens(os.getenv(f"{prefix}_MODE_VOLTAGE_TOKENS", "volt,vdc,voltage")),
        current_mode_tokens=_csv_tokens(os.getenv(f"{prefix}_MODE_CURRENT_TOKENS", "curr,adc,current,amp")),
        resistance_mode_tokens=_csv_tokens(os.getenv(f"{prefix}_MODE_RESISTANCE_TOKENS", "ohm,res,resistance")),
        temperature_mode_tokens=_csv_tokens(os.getenv(f"{prefix}_MODE_TEMPERATURE_TOKENS", "temp,temperature")),
        frequency_mode_tokens=_csv_tokens(os.getenv(f"{prefix}_MODE_FREQUENCY_TOKENS", "freq,frequency,hz")),
        capacitance_mode_tokens=_csv_tokens(os.getenv(f"{prefix}_MODE_CAPACITANCE_TOKENS", "cap,capacitance,farad")),
        voltage_set_mode_command=os.getenv(f"{prefix}_SET_MODE_VOLTAGE_COMMAND", "").strip(),
        current_set_mode_command=os.getenv(f"{prefix}_SET_MODE_CURRENT_COMMAND", "").strip(),
        resistance_set_mode_command=os.getenv(f"{prefix}_SET_MODE_RESISTANCE_COMMAND", "").strip(),
        temperature_set_mode_command=os.getenv(f"{prefix}_SET_MODE_TEMPERATURE_COMMAND", "").strip(),
        frequency_set_mode_command=os.getenv(f"{prefix}_SET_MODE_FREQUENCY_COMMAND", "").strip(),
        capacitance_set_mode_command=os.getenv(f"{prefix}_SET_MODE_CAPACITANCE_COMMAND", "").strip(),
        temperature_display_command=os.getenv(f"{prefix}_TEMPERATURE_DISPLAY_COMMAND", "").strip(),
        temperature_unit_command=os.getenv(f"{prefix}_TEMPERATURE_UNIT_COMMAND", "").strip(),
    )


def _csv_tokens(raw: str) -> tuple[str, ...]:
    return tuple(token.strip().lower() for token in raw.split(",") if token.strip())


def _power_source_from_env() -> PowerSourceConfig:
    driver = os.getenv("POWER_SOURCE_DRIVER", "visa").strip().lower()
    if driver not in {"serial", "visa"}:
        raise ValueError(f"Invalid driver '{driver}' for POWER_SOURCE_DRIVER")

    return PowerSourceConfig(
        name=os.getenv("POWER_SOURCE_NAME", "Multicomp Pro 72-2540").strip()
        or "Multicomp Pro 72-2540",
        enabled=os.getenv("POWER_SOURCE_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"},
        driver=driver,
        identifier=os.getenv("POWER_SOURCE_IDENTIFIER", "").strip(),
        baudrate=int(os.getenv("POWER_SOURCE_BAUDRATE", "9600")),
        measure_voltage_command=(
            os.getenv("POWER_SOURCE_MEASURE_VOLTAGE_COMMAND", "MEAS:VOLT?").strip() or "MEAS:VOLT?"
        ),
        measure_current_command=(
            os.getenv("POWER_SOURCE_MEASURE_CURRENT_COMMAND", "MEAS:CURR?").strip() or "MEAS:CURR?"
        ),
        read_set_voltage_command=(
            os.getenv("POWER_SOURCE_READ_SET_VOLTAGE_COMMAND", "VOLT?").strip() or "VOLT?"
        ),
        read_set_current_command=(
            os.getenv("POWER_SOURCE_READ_SET_CURRENT_COMMAND", "CURR?").strip() or "CURR?"
        ),
        set_voltage_command=(
            os.getenv("POWER_SOURCE_SET_VOLTAGE_COMMAND", "VOLT {value}").strip() or "VOLT {value}"
        ),
        set_current_command=(
            os.getenv("POWER_SOURCE_SET_CURRENT_COMMAND", "CURR {value}").strip() or "CURR {value}"
        ),
        read_output_enabled_command=(
            os.getenv("POWER_SOURCE_READ_OUTPUT_ENABLED_COMMAND", "OUTP?").strip() or "OUTP?"
        ),
        set_output_enabled_command=(
            os.getenv("POWER_SOURCE_SET_OUTPUT_ENABLED_COMMAND", "OUTP {state}").strip()
            or "OUTP {state}"
        ),
    )


def load_config() -> AppConfig:
    root_dir = Path(__file__).resolve().parents[2]
    env_path = root_dir / ".env"
    env_example_path = root_dir / ".env.example"

    if env_path.exists():
        load_dotenv(env_path)
    elif env_example_path.exists():
        load_dotenv(env_example_path)
    else:
        load_dotenv()

    return AppConfig(
        host=os.getenv("HOST", "127.0.0.1").strip(),
        port=int(os.getenv("PORT", "8000")),
        acquisition_interval_seconds=float(os.getenv("ACQUISITION_INTERVAL_SECONDS", "0.25")),
        ui_refresh_interval_seconds=float(os.getenv("UI_REFRESH_INTERVAL_SECONDS", os.getenv("POLL_INTERVAL_SECONDS", "0.5"))),
        window_seconds=int(os.getenv("WINDOW_SECONDS", "60")),
        meter_1=_meter_from_env("METER1", "MP730889"),
        meter_2=_meter_from_env("METER2", "MP730027"),
        power_source=_power_source_from_env(),
    )
