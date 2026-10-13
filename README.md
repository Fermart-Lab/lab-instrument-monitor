# Lab Instrument Monitor

Fermart lab's instrument acquisition and control application, originally developed
as **SurfaceConductivity**. The repository is `Fermart-Lab/lab-instrument-monitor`;
existing Python package and launcher names are retained for compatibility.

Python project for Linux Mint to read two USB multimeters and show:
- live value for each meter, with switchable voltage/current/resistance/temperature/frequency/capacitance mode
- continuously updating line graphs for the last 60 seconds
- programmable power source controls (set voltage/current and output ON/OFF)
- recording to CSV or Excel

## Supported meter adapters
- `serial` (USB serial ports such as `/dev/ttyUSB0`)
- `visa` (USBTMC/VISA resource strings)

Each meter can use its own driver.

## Project structure
- `src/surface_conductivity_monitor/desktop.py`: GTK desktop shell with embedded WebKit view
- `src/surface_conductivity_monitor/main.py`: FastAPI server
- `src/surface_conductivity_monitor/service.py`: polling + rolling 60-second buffers
- `src/surface_conductivity_monitor/meters.py`: meter + power source drivers (serial/visa)
- `src/surface_conductivity_monitor/templates/index.html`: dashboard page
- `src/surface_conductivity_monitor/static/app.js`: live updates and chart rendering

## Setup (Linux Mint)
1. Create virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The desktop shell also needs the Linux GTK/WebKit runtime that ships with Linux Mint. If the desktop window does not open, install the GTK WebKit package for your distro.

2. Configure environment:

```bash
cp .env.example .env
```

Edit `.env` with the real connection values for your devices:
- `ACQUISITION_INTERVAL_SECONDS` controls device sampling and recording speed
- `UI_REFRESH_INTERVAL_SECONDS` controls how often the UI refreshes
- `METER1_IDENTIFIER` and `METER2_IDENTIFIER` (prefer stable `/dev/serial/by-id/...` paths)
- set `METER*_INITIAL_MODE` and the per-mode `METER*_{VOLTAGE|CURRENT|RESISTANCE|TEMPERATURE|FREQUENCY|CAPACITANCE}_READ_COMMAND` values your meter accepts
- if needed, slow front-panel mode polling with `METER*_MODE_SYNC_INTERVAL_SECONDS` for instruments that dislike frequent mode queries
- set `METER*_MODE_SYNC_INTERVAL_SECONDS=0` to check the front-panel mode on every acquisition cycle, which can help VISA meters follow manual device changes without snapping back
- if the meter can report its active function, set `METER*_READ_MODE_COMMAND` and optionally adjust the `METER*_MODE_*_TOKENS` match lists
- if the instrument requires an explicit mode switch command, also set `METER*_SET_MODE_{VOLTAGE|CURRENT|RESISTANCE|TEMPERATURE|FREQUENCY|CAPACITANCE}_COMMAND`
- for XDM-style meters that use dual displays in temperature mode, `MEAS1?` may return the converted temperature shown on the main display while `READ?` can still reflect the raw measurement; use `METER*_TEMPERATURE_READ_COMMAND=MEAS1?` together with `METER*_TEMPERATURE_DISPLAY_COMMAND` and `METER*_TEMPERATURE_UNIT_COMMAND` when needed
- if the meter is VISA/USBTMC, set `METER*_DRIVER=visa` and provide VISA resource string
- set `POWER_SOURCE_*` variables for your programmable supply (driver, identifier, and SCPI commands)
- legacy `METER*_READ_COMMAND` is still accepted as a fallback for all modes
- for MP730889, known-good defaults are `METER*_BAUDRATE=115200` and `METER*_VOLTAGE_READ_COMMAND=MEAS?`
- for MP730027 on Linux, known-good settings are `METER*_DRIVER=visa`, `METER*_IDENTIFIER=USB0::0x5345::0x1234::REPLACE_WITH_SERIAL::INSTR`, `METER*_VOLTAGE_READ_COMMAND=MEAS?`

You can list serial paths with:

```bash
ls -l /dev/serial/by-id /dev/ttyUSB* /dev/ttyACM*
```

3. Run the desktop app:

```bash
python3 run.py
```

This opens the monitor in a native Linux window.

4. Optional: run server-only mode for debugging in a browser:

```bash
python3 run.py --server
```

Then open:
- `http://127.0.0.1:8000`

## Desktop launcher
- `SurfaceConductivityMonitor.desktop`: Linux launcher file for double-click use
- `launch_surface_conductivity_monitor.sh`: helper script used by the desktop launcher

To add the launcher to your desktop or app menu on Linux Mint:

```bash
cp SurfaceConductivityMonitor.desktop ~/Desktop/
chmod +x ~/Desktop/SurfaceConductivityMonitor.desktop
```

You can also copy it into `~/.local/share/applications/` to show it in the menu:

```bash
cp SurfaceConductivityMonitor.desktop ~/.local/share/applications/
```

## Notes for MP730889 and MP730027
- This starter assumes SCPI-like query commands (`READ?`, `MEAS:CURR?`, `MEAS:RES?`) and ASCII numeric responses.
- Bidirectional sync of meter mode requires a working `METER*_READ_MODE_COMMAND`. Without it, the UI can request mode changes but cannot detect manual front-panel changes on the device.
- If those models use a different USB protocol/command set, keep the UI and polling service as-is and update only the per-mode commands or adapter in `meters.py`.
- If access permissions fail on `/dev/ttyUSB*`, add your user to the `dialout` group and reconnect:

```bash
sudo usermod -aG dialout "$USER"
```

Then log out/in.

## Quick troubleshooting
- No live data and no errors: check meter USB path in `.env`.
- `Startup connection error`: backend could not open the configured real device.
- `invalid response`: command/baudrate mismatch or non-numeric response from meter.
- Power source panel disabled: check `POWER_SOURCE_ENABLED=1` and valid `POWER_SOURCE_IDENTIFIER`.
- `*IDN?` returns a different model than expected: verify physical wiring; Linux may see OEM names (for example, `72-2540`) instead of retail part numbers.

For VISA backend errors like `Could not locate a VISA implementation` or `Wrapper not found: pyvisa_py`:

```bash
pip install pyvisa-py pyusb
```

## Development and validation

Python 3.10+ is required; CI uses Python 3.12. The current application uses FastAPI.
`src/main.py` is the retained legacy Dash prototype; its dependencies are optional.

```bash
python -m pip install -e '.[dev,legacy]'
python -m pytest -q
```

The existing tests cover legacy parsing and buffering. Additional recording tests
exercise the current application without hardware. CI also checks syntax and wheel
building. Device integration and GTK display behavior require Linux lab validation.
For desktop mode, create the virtual environment with `--system-site-packages` if
GTK bindings are supplied by the operating system.

Desktop launcher files are templates: replace `/absolute/path/to/SurfaceConductivity`
with your checkout path before installing them. Experimental recordings and `.env`
are intentionally excluded. No first-party open-source license has been selected.
