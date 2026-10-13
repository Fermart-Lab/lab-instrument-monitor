from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import serial

from .config import MeasurementMode, MeterConfig, PowerSourceConfig, SUPPORTED_MEASUREMENT_MODES

try:
    import pyvisa
except ImportError:  # pragma: no cover
    pyvisa = None

_FLOAT_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
_SERIAL_SET_MODE_FALLBACKS: dict[MeasurementMode, list[str]] = {
    "voltage": ["CONF:VOLT:DC", 'FUNC "VOLT"', "FUNC VOLT"],
    "current": ["CONF:CURR:DC", 'FUNC "CURR"', "FUNC CURR"],
    "resistance": ["CONF:RES", 'FUNC "RES"', "FUNC RES"],
    "temperature": ["CONF:TEMP:RTD", "CONF:TEMP", 'FUNC "TEMP"', "FUNC TEMP"],
    "frequency": ["CONF:FREQ", 'FUNC "FREQ"', "FUNC FREQ"],
    "capacitance": ["CONF:CAP", 'FUNC "CAP"', "FUNC CAP"],
}
_VISA_SET_MODE_FALLBACKS: dict[MeasurementMode, list[str]] = {
    "voltage": ['FUNC "VOLT"', "FUNC VOLT", "CONF:VOLT:DC"],
    "current": ['FUNC "CURR"', "FUNC CURR", "CONF:CURR:DC"],
    "resistance": ['FUNC "RES"', "FUNC RES", "CONF:RES"],
    "temperature": ['FUNC "TEMP"', "FUNC TEMP", "CONF:TEMP:RTD", "CONF:TEMP"],
    "frequency": ['FUNC "FREQ"', "FUNC FREQ", "CONF:FREQ"],
    "capacitance": ['FUNC "CAP"', "FUNC CAP", "CONF:CAP"],
}


class MeterReadError(RuntimeError):
    pass


class PowerSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class MeasurementReading:
    value: float
    display_unit: str
    quantity: MeasurementMode


class MeterDevice:
    def read_mode(self) -> Optional[MeasurementMode]:
        return None

    def read_measurement(self, mode: MeasurementMode) -> MeasurementReading:
        value = self.read_value(mode)
        return MeasurementReading(value=value, display_unit=_default_unit_for_mode(mode), quantity=mode)

    def read_value(self, mode: MeasurementMode) -> float:
        raise NotImplementedError

    def set_mode(self, mode: MeasurementMode) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


class PowerSourceDevice:
    def read_output_voltage(self) -> float:
        raise NotImplementedError

    def read_output_current(self) -> float:
        raise NotImplementedError

    def read_set_voltage(self) -> float:
        raise NotImplementedError

    def read_set_current(self) -> float:
        raise NotImplementedError

    def is_output_enabled(self) -> bool:
        raise NotImplementedError

    def set_voltage(self, value: float) -> None:
        raise NotImplementedError

    def set_current(self, value: float) -> None:
        raise NotImplementedError

    def set_output_enabled(self, enabled: bool) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


@dataclass
class SerialMeter(MeterDevice):
    config: MeterConfig
    QUERY_RETRY_COUNT = 3
    QUERY_RETRY_DELAY_SECONDS = 0.12
    MODE_SWITCH_SETTLE_SECONDS = 0.2
    MODE_SWITCH_PRIME_READS = 1
    RESPONSE_INITIAL_DELAY_SECONDS = 0.08
    RESPONSE_IDLE_SECONDS = 0.08
    RESPONSE_MAX_SECONDS = 1.5

    def __post_init__(self) -> None:
        if not self.config.identifier:
            raise MeterReadError(f"{self.config.name}: serial identifier (port) is empty")
        try:
            self._serial = serial.Serial(
                port=self.config.identifier,
                baudrate=self.config.baudrate,
                timeout=1,
                write_timeout=1,
            )
            self._autotune_attempted = False
        except (serial.SerialException, OSError) as exc:
            available = ", ".join(_available_serial_ports()) or "(none)"
            raise MeterReadError(
                f"{self.config.name}: cannot open serial port '{self.config.identifier}' "
                f"(baud={self.config.baudrate}). Available ports: {available}. Original error: {exc}"
            ) from exc

    def read_value(self, mode: MeasurementMode) -> float:
        return self.read_measurement(mode).value

    def read_measurement(self, mode: MeasurementMode) -> MeasurementReading:
        last_raw = ""
        last_command = self.config.read_command_for_mode(mode)
        for command in self._value_command_candidates(mode):
            raw = self._query(command)
            reading = _parse_measurement_reading(raw, mode)
            if reading is not None:
                return reading
            last_raw = raw
            last_command = command

        if not self._autotune_attempted:
            self._autotune_attempted = True
            tuned = self._try_autotune(mode)
            if tuned is not None:
                return tuned

        display = last_raw if last_raw else "<empty>"
        raise MeterReadError(
            f"{self.config.name}: invalid response '{display}' from '{self.config.identifier}' "
            f"(baud={self._serial.baudrate}, command='{last_command}')"
        )

    def read_mode(self) -> Optional[MeasurementMode]:
        if not self.config.read_mode_command:
            return None
        raw = self._query(self.config.read_mode_command)
        mode = _parse_mode(raw, self.config)
        if mode is None:
            raise MeterReadError(f"{self.config.name}: invalid mode response '{raw}'")
        return mode

    def set_mode(self, mode: MeasurementMode) -> None:
        command = self.config.set_mode_command_for_mode(mode)
        if command:
            self._write(command)
            for post_command in self.config.post_set_commands_for_mode(mode):
                self._write(post_command)
            time.sleep(self.MODE_SWITCH_SETTLE_SECONDS)
            read_command = self.config.read_command_for_mode(mode)
            for _ in range(self.MODE_SWITCH_PRIME_READS):
                try:
                    self._query(read_command)
                except Exception:
                    break
            return

        for fallback in self._mode_command_candidates(mode):
            try:
                self._write(fallback)
                for post_command in self.config.post_set_commands_for_mode(mode):
                    self._write(post_command)
                time.sleep(self.MODE_SWITCH_SETTLE_SECONDS)
                return
            except Exception:
                continue

    def close(self) -> None:
        self._serial.close()

    def _write(self, command: str) -> None:
        payload = command
        if not payload.endswith("\n"):
            payload += "\n"
        self._serial.write(payload.encode("ascii", errors="ignore"))

    def _query(self, command: str) -> str:
        payload = command
        if not payload.endswith("\n"):
            payload += "\n"
        for attempt in range(self.QUERY_RETRY_COUNT):
            self._serial.reset_input_buffer()
            self._serial.write(payload.encode("ascii", errors="ignore"))
            raw = self._read_response_text()
            if raw:
                return raw
            if attempt + 1 < self.QUERY_RETRY_COUNT:
                time.sleep(self.QUERY_RETRY_DELAY_SECONDS)
        return ""

    def _read_response_text(self) -> str:
        deadline = time.monotonic() + self.RESPONSE_MAX_SECONDS
        time.sleep(self.RESPONSE_INITIAL_DELAY_SECONDS)
        chunks: list[str] = []
        last_data_at = time.monotonic()

        while time.monotonic() < deadline:
            waiting = self._serial.in_waiting
            if waiting:
                data = self._serial.read(waiting).decode("ascii", errors="ignore")
                if data:
                    chunks.append(data)
                    last_data_at = time.monotonic()
                    continue
            if chunks and time.monotonic() - last_data_at >= self.RESPONSE_IDLE_SECONDS:
                break
            time.sleep(0.02)

        return "".join(chunks).strip()

    def _value_command_candidates(self, mode: MeasurementMode) -> list[str]:
        return _value_command_candidates(mode, self.config.read_command_for_mode(mode), visa_style=False)

    def _mode_command_candidates(self, mode: MeasurementMode) -> list[str]:
        return _SERIAL_SET_MODE_FALLBACKS[mode]

    def _reopen(self, baudrate: int) -> None:
        self._serial.close()
        self._serial = serial.Serial(
            port=self.config.identifier,
            baudrate=baudrate,
            timeout=1,
            write_timeout=1,
        )

    def _try_autotune(self, mode: MeasurementMode) -> Optional[MeasurementReading]:
        original_baud = int(self._serial.baudrate)
        baud_candidates = _ordered_unique([original_baud, 115200, 9600, 19200, 2400])
        command_candidates = _ordered_unique([*self._value_command_candidates(mode), "MEAS?", "READ?"])

        for baud in baud_candidates:
            try:
                if int(self._serial.baudrate) != baud:
                    self._reopen(baud)
            except (serial.SerialException, OSError):
                continue

            for command in command_candidates:
                try:
                    raw = self._query(command)
                except (serial.SerialException, OSError):
                    continue
                reading = _parse_measurement_reading(raw, mode)
                if reading is None:
                    continue
                return reading

        try:
            if int(self._serial.baudrate) != original_baud:
                self._reopen(original_baud)
        except (serial.SerialException, OSError):
            return None
        return None


@dataclass
class VisaMeter(MeterDevice):
    config: MeterConfig
    QUERY_RETRY_COUNT = 3
    QUERY_RETRY_DELAY_SECONDS = 0.08
    MODE_SWITCH_SETTLE_SECONDS = 0.2

    def __post_init__(self) -> None:
        if pyvisa is None:
            raise MeterReadError(
                "pyvisa is not installed. "
                f"Install VISA packages in this interpreter ({sys.executable}) with: "
                f"`{sys.executable} -m pip install pyvisa pyvisa-py pyusb`"
            )
        if not self.config.identifier:
            raise MeterReadError(f"{self.config.name}: VISA resource is empty")

        self._rm = None
        errors: list[str] = []
        for backend in (None, "@py"):
            try:
                self._rm = pyvisa.ResourceManager() if backend is None else pyvisa.ResourceManager(backend)
                self._inst = self._rm.open_resource(self.config.identifier)
                self._inst.timeout = 1000
                if self.config.identifier.upper().startswith("ASRL"):
                    # 72-2540 serial-VISA replies are line-based; force LF to avoid
                    # mixed/shifted frames when backend defaults to CRLF.
                    self._inst.read_termination = "\n"
                    self._inst.write_termination = "\n"
                return
            except Exception as exc:
                errors.append(f"{backend or 'default'} backend: {exc}")
                if self._rm is not None:
                    try:
                        self._rm.close()
                    except Exception:
                        pass
                self._rm = None

        details = " | ".join(errors)
        hint = ""
        details_lc = details.lower()
        if "could not locate a visa implementation" in details_lc or "wrapper not found" in details_lc:
            hint = (
                " Install a VISA backend in this same Python environment "
                f"({sys.executable}) with: `{sys.executable} -m pip install pyvisa pyvisa-py pyusb`."
            )
        raise MeterReadError(
            f"{self.config.name}: cannot open VISA resource '{self.config.identifier}'. {details}{hint}"
        )

    def read_value(self, mode: MeasurementMode) -> float:
        return self.read_measurement(mode).value

    def read_measurement(self, mode: MeasurementMode) -> MeasurementReading:
        last_raw = ""
        for command in self._value_command_candidates(mode):
            raw = self._query_text(command)
            reading = _parse_measurement_reading(raw, mode)
            if reading is not None:
                return reading
            last_raw = raw
        raise MeterReadError(f"{self.config.name}: invalid response '{last_raw}'")

    def read_mode(self) -> Optional[MeasurementMode]:
        if not self.config.read_mode_command:
            return None
        raw = self._query_text(self.config.read_mode_command)
        mode = _parse_mode(raw, self.config)
        if mode is None:
            raise MeterReadError(f"{self.config.name}: invalid mode response '{raw}'")
        return mode

    def set_mode(self, mode: MeasurementMode) -> None:
        command = self.config.set_mode_command_for_mode(mode)
        if command:
            self._inst.write(command)
            for post_command in self.config.post_set_commands_for_mode(mode):
                self._inst.write(post_command)
            time.sleep(self.MODE_SWITCH_SETTLE_SECONDS)
            return
        for fallback in self._mode_command_candidates(mode):
            self._inst.write(fallback)
            for post_command in self.config.post_set_commands_for_mode(mode):
                self._inst.write(post_command)
            time.sleep(self.MODE_SWITCH_SETTLE_SECONDS)
            return

    def _mode_command_candidates(self, mode: MeasurementMode) -> list[str]:
        return _VISA_SET_MODE_FALLBACKS[mode]

    def _value_command_candidates(self, mode: MeasurementMode) -> list[str]:
        return _value_command_candidates(mode, self.config.read_command_for_mode(mode), visa_style=True)

    def close(self) -> None:
        try:
            self._inst.close()
        finally:
            self._rm.close()

    def _query_text(self, command: str) -> str:
        for attempt in range(self.QUERY_RETRY_COUNT):
            raw = str(self._inst.query(command)).strip()
            if raw:
                return raw
            if attempt + 1 < self.QUERY_RETRY_COUNT:
                time.sleep(self.QUERY_RETRY_DELAY_SECONDS)
        return ""


@dataclass
class SerialPowerSource(PowerSourceDevice):
    config: PowerSourceConfig

    def __post_init__(self) -> None:
        if not self.config.identifier:
            raise PowerSourceError("Power source serial identifier (port) is empty")
        try:
            self._serial = serial.Serial(
                port=self.config.identifier,
                baudrate=self.config.baudrate,
                timeout=1,
                write_timeout=1,
            )
        except (serial.SerialException, OSError) as exc:
            available = ", ".join(_available_serial_ports()) or "(none)"
            raise PowerSourceError(
                f"{self.config.name}: cannot open serial port '{self.config.identifier}' "
                f"(baud={self.config.baudrate}). Available ports: {available}. Original error: {exc}"
            ) from exc

    def read_output_voltage(self) -> float:
        return self._query_float(self.config.measure_voltage_command, "measured voltage")

    def read_output_current(self) -> float:
        return self._query_float(self.config.measure_current_command, "measured current")

    def read_set_voltage(self) -> float:
        return self._query_float(self.config.read_set_voltage_command, "set voltage")

    def read_set_current(self) -> float:
        return self._query_float(self.config.read_set_current_command, "set current")

    def is_output_enabled(self) -> bool:
        raw = self._query(self.config.read_output_enabled_command)
        parsed = _parse_bool(raw)
        if parsed is None:
            raise PowerSourceError(f"{self.config.name}: invalid output state response '{raw}'")
        return parsed

    def set_voltage(self, value: float) -> None:
        self._write(_format_value_command(self.config.set_voltage_command, value))

    def set_current(self, value: float) -> None:
        self._write(_format_value_command(self.config.set_current_command, value))

    def set_output_enabled(self, enabled: bool) -> None:
        self._write(_format_state_command(self.config.set_output_enabled_command, enabled))

    def close(self) -> None:
        self._serial.close()

    def _write(self, command: str) -> None:
        payload = command if command.endswith("\n") else f"{command}\n"
        self._serial.write(payload.encode("ascii", errors="ignore"))

    def _query(self, command: str) -> str:
        payload = command if command.endswith("\n") else f"{command}\n"
        self._serial.reset_input_buffer()
        self._serial.write(payload.encode("ascii", errors="ignore"))
        return self._serial.readline().decode("ascii", errors="ignore").strip()

    def _query_float(self, command: str, label: str) -> float:
        raw = self._query(command)
        value = _parse_float(raw)
        if value is None:
            raise PowerSourceError(f"{self.config.name}: invalid {label} response '{raw}'")
        return value


@dataclass
class VisaPowerSource(PowerSourceDevice):
    config: PowerSourceConfig

    def __post_init__(self) -> None:
        if pyvisa is None:
            raise PowerSourceError(
                "pyvisa is not installed. "
                f"Install VISA packages in this interpreter ({sys.executable}) with: "
                f"`{sys.executable} -m pip install pyvisa pyvisa-py pyusb`"
            )
        if not self.config.identifier:
            raise PowerSourceError(f"{self.config.name}: VISA resource is empty")

        self._rm = None
        errors: list[str] = []
        for backend in (None, "@py"):
            try:
                self._rm = pyvisa.ResourceManager() if backend is None else pyvisa.ResourceManager(backend)
                self._inst = self._rm.open_resource(self.config.identifier)
                self._inst.timeout = 1000
                return
            except Exception as exc:
                errors.append(f"{backend or 'default'} backend: {exc}")
                if self._rm is not None:
                    try:
                        self._rm.close()
                    except Exception:
                        pass
                self._rm = None

        details = " | ".join(errors)
        hint = ""
        details_lc = details.lower()
        if "could not locate a visa implementation" in details_lc or "wrapper not found" in details_lc:
            hint = (
                " Install a VISA backend in this same Python environment "
                f"({sys.executable}) with: `{sys.executable} -m pip install pyvisa pyvisa-py pyusb`."
            )
        raise PowerSourceError(
            f"{self.config.name}: cannot open VISA resource '{self.config.identifier}'. {details}{hint}"
        )

    def read_output_voltage(self) -> float:
        return self._query_float(self.config.measure_voltage_command, "measured voltage")

    def read_output_current(self) -> float:
        return self._query_float(self.config.measure_current_command, "measured current")

    def read_set_voltage(self) -> float:
        return self._query_float(self.config.read_set_voltage_command, "set voltage")

    def read_set_current(self) -> float:
        return self._query_float(self.config.read_set_current_command, "set current")

    def is_output_enabled(self) -> bool:
        raw = self._query_text(self.config.read_output_enabled_command)
        parsed = _parse_bool(raw)
        if parsed is None:
            raise PowerSourceError(f"{self.config.name}: invalid output state response '{raw}'")
        return parsed

    def set_voltage(self, value: float) -> None:
        self._inst.write(_format_value_command(self.config.set_voltage_command, value))

    def set_current(self, value: float) -> None:
        self._inst.write(_format_value_command(self.config.set_current_command, value))

    def set_output_enabled(self, enabled: bool) -> None:
        self._inst.write(_format_state_command(self.config.set_output_enabled_command, enabled))

    def close(self) -> None:
        try:
            self._inst.close()
        finally:
            self._rm.close()

    def _query_float(self, command: str, label: str) -> float:
        raw = self._query_text(command)
        value = _parse_float(raw)
        if value is None:
            raise PowerSourceError(f"{self.config.name}: invalid {label} response '{raw}'")
        return value

    def _query_text(self, command: str) -> str:
        for _ in range(3):
            raw = str(self._inst.query(command)).strip()
            if raw:
                return raw
            time.sleep(0.03)
        return ""


def _parse_float(text: str) -> Optional[float]:
    match = _FLOAT_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _default_unit_for_mode(mode: MeasurementMode) -> str:
    if mode == "voltage":
        return "V"
    if mode == "current":
        return "A"
    if mode == "resistance":
        return "Ohm"
    if mode == "temperature":
        return "C"
    if mode == "frequency":
        return "Hz"
    return "F"


def _parse_measurement_reading(text: str, mode: MeasurementMode) -> Optional[MeasurementReading]:
    value = _parse_float(text)
    if value is None:
        return None
    return MeasurementReading(
        value=value,
        display_unit=_extract_display_unit(text, mode),
        quantity=mode,
    )


def _extract_display_unit(text: str, mode: MeasurementMode) -> str:
    raw = text.strip()
    if not raw:
        return _default_unit_for_mode(mode)

    unit_aliases = {
        "voltage": (
            (r"(?i)\b([pnumkMG]?V)(?:DC|AC)?\b", None),
            (r"(?i)\b(volts?)\b", "V"),
        ),
        "current": (
            (r"(?i)\b([pnumkMG]?A)(?:DC|AC)?\b", None),
            (r"(?i)\b(amps?|amperes?)\b", "A"),
        ),
        "resistance": (
            (r"(?i)\b([pnumkMG]?(?:ohm|ohms|Ω))\b", None),
            (r"(?i)\b(resistance)\b", "Ohm"),
        ),
        "temperature": (
            (r"(?i)(?:°\s*)?([CFK])\b", None),
            (r"(?i)\b(celsius|fahrenheit|kelvin)\b", None),
        ),
        "frequency": (
            (r"(?i)\b([pnumkMG]?Hz)\b", None),
            (r"(?i)\b(frequency)\b", "Hz"),
        ),
        "capacitance": (
            (r"(?i)\b([pnumkMG]?(?:F|farad|farads))\b", None),
            (r"(?i)\b(capacitance)\b", "F"),
        ),
    }

    for pattern, forced_unit in unit_aliases[mode]:
        match = re.search(pattern, raw)
        if not match:
            continue
        return _normalize_unit_token(forced_unit or match.group(1), mode)
    return _default_unit_for_mode(mode)


def _normalize_unit_token(token: str, mode: MeasurementMode) -> str:
    compact = (
        token.strip()
        .replace(" ", "")
        .replace("μ", "u")
        .replace("Ω", "Ohm")
        .replace("°", "")
    )
    prefix = ""

    if mode == "resistance":
        if compact.endswith("Ohms"):
            prefix = compact[:-4]
        elif compact.endswith("Ohm"):
            prefix = compact[:-3]
        else:
            prefix = compact
    elif mode == "capacitance":
        lowered = compact.lower()
        if lowered.endswith("farads"):
            prefix = compact[:-6]
        elif lowered.endswith("farad"):
            prefix = compact[:-5]
        elif compact.endswith("F"):
            prefix = compact[:-1]
        else:
            prefix = compact
    elif mode == "temperature":
        upper = compact.upper()
        if upper.endswith(("C", "F", "K")):
            return upper[-1]
        return _default_unit_for_mode(mode)
    elif mode == "frequency":
        if compact.lower().endswith("hz"):
            prefix = compact[:-2]
        else:
            prefix = compact
    elif compact.lower().endswith("v"):
        prefix = compact[:-1]
    elif compact.lower().endswith("a"):
        prefix = compact[:-1]

    prefix_map = {
        "p": "p",
        "n": "n",
        "u": "u",
        "m": "m",
        "": "",
        "k": "k",
        "K": "k",
        "M": "M",
        "meg": "M",
        "Meg": "M",
        "MEG": "M",
        "g": "G",
        "G": "G",
    }
    rendered_prefix = prefix_map.get(prefix, prefix[:1])
    if mode == "voltage":
        return f"{rendered_prefix}V"
    if mode == "current":
        return f"{rendered_prefix}A"
    if mode == "resistance":
        return f"{rendered_prefix}Ohm"
    if mode == "frequency":
        return f"{rendered_prefix}Hz"
    return f"{rendered_prefix}F"


def _available_serial_ports() -> list[str]:
    ports = {
        str(path)
        for pattern in ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*")
        for path in Path("/").glob(pattern.lstrip("/"))
    }
    return sorted(ports)


def _value_command_candidates(
    mode: MeasurementMode,
    primary: str,
    *,
    visa_style: bool,
) -> list[str]:
    extras_by_mode: dict[MeasurementMode, list[str]] = {
        "voltage": ["MEAS:VOLT?", "MEAS:VOLT:DC?", "READ?"],
        "current": ["MEAS:CURR?", "MEAS:CURR:DC?", "MEAS?"] + (["READ?"] if visa_style else []),
        "resistance": ["MEAS:RES?"] + (["READ?"] if visa_style else []),
        "temperature": ["MEAS1?", "MEAS:TEMP:RTD?", "MEAS:TEMP?", "READ?"],
        "frequency": ["MEAS:FREQ?", "READ?"],
        "capacitance": ["MEAS:CAP?", "READ?"],
    }
    return [str(cmd) for cmd in _ordered_unique([primary, *extras_by_mode[mode]])]


def _ordered_unique(values: list[object]) -> list[object]:
    seen: set[object] = set()
    out: list[object] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _parse_bool(text: str) -> Optional[bool]:
    raw_original = text.strip()
    raw = raw_original.lower()
    if raw in {"1", "on", "true"}:
        return True
    if raw in {"0", "off", "false"}:
        return False

    value = _parse_float(raw)
    if value is None:
        if len(raw_original) == 1:
            # Korad-like power supplies return a single status byte for STATUS?.
            # Output enabled is encoded in bit 6 (0x40).
            return bool(ord(raw_original) & 0x40)
        return None
    return abs(value) > 1e-12


def _parse_mode(text: str, config: MeterConfig) -> Optional[MeasurementMode]:
    raw = text.strip().lower()
    if not raw:
        return None
    for mode in SUPPORTED_MEASUREMENT_MODES:
        tokens = config.mode_tokens_for_mode(mode)
        if any(token and token in raw for token in tokens):
            return mode
    return None


def _format_value_command(template: str, value: float) -> str:
    rendered_value = f"{value:.6f}"
    if "{value}" in template:
        return template.format(value=rendered_value)
    return f"{template} {rendered_value}"


def _format_state_command(template: str, enabled: bool) -> str:
    state = "ON" if enabled else "OFF"
    state_num = "1" if enabled else "0"
    if "{state}" in template:
        return template.format(state=state)
    if "{state_num}" in template:
        return template.format(state_num=state_num)
    return f"{template} {state}"


def build_meter(config: MeterConfig) -> MeterDevice:
    if config.driver == "serial":
        return SerialMeter(config)
    if config.driver == "visa":
        return VisaMeter(config)
    raise MeterReadError(f"Unsupported driver: {config.driver}")


def build_power_source(config: PowerSourceConfig) -> PowerSourceDevice:
    if config.driver == "serial":
        return SerialPowerSource(config)
    if config.driver == "visa":
        return VisaPowerSource(config)
    raise PowerSourceError(f"Unsupported driver: {config.driver}")
