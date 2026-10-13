"""Live web UI for reading two USB multimeters and plotting last 60 seconds."""

from __future__ import annotations

import argparse
import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, List, Optional, Tuple

from dash import Dash, Input, Output, dcc, html
import plotly.graph_objects as go

try:
    import serial
except ImportError:  # pragma: no cover - handled at runtime
    serial = None

Point = Tuple[datetime, float]


def extract_numeric_value(text: str) -> Optional[float]:
    """Extract the first numeric value from a device response string."""
    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def trim_points(points: Deque[Point], window_seconds: int) -> None:
    """Keep only points from the last ``window_seconds`` seconds."""
    threshold = time.time() - window_seconds
    while points and points[0][0].timestamp() < threshold:
        points.popleft()


@dataclass
class MultimeterReader:
    """Background USB serial reader for one meter."""

    name: str
    port: str
    baudrate: int = 9600
    timeout: float = 0.5
    query: Optional[str] = "MEAS?"
    simulated: bool = False
    points: Deque[Point] = field(default_factory=lambda: deque(maxlen=5000))
    latest_value: Optional[float] = None
    last_error: Optional[str] = None

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._running = True
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=2.0)

    def get_points(self, window_seconds: int) -> List[Point]:
        with self._lock:
            trim_points(self.points, window_seconds)
            return list(self.points)

    def _append_value(self, value: float) -> None:
        now = datetime.now()
        with self._lock:
            self.latest_value = value
            self.last_error = None
            self.points.append((now, value))
            trim_points(self.points, 60)

    def _set_error(self, message: str) -> None:
        with self._lock:
            self.last_error = message

    def _run_simulated(self) -> None:
        base = random.uniform(0.2, 2.0)
        phase = random.uniform(0.0, 6.28)
        t = 0.0
        while self._running:
            value = base + (0.15 * random.uniform(-1.0, 1.0)) + (0.2 * (t % 6.28))
            self._append_value(round(value + phase * 0.01, 5))
            t += 0.2
            time.sleep(0.2)

    def _run(self) -> None:
        if self.simulated:
            self._run_simulated()
            return

        if serial is None:
            self._set_error("pyserial is not installed.")
            return

        while self._running:
            try:
                with serial.Serial(self.port, self.baudrate, timeout=self.timeout) as ser:
                    while self._running:
                        if self.query:
                            ser.write((self.query + "\r\n").encode("ascii", errors="ignore"))
                        raw = ser.readline().decode("utf-8", errors="ignore").strip()
                        value = extract_numeric_value(raw)
                        if value is not None:
                            self._append_value(value)
                        time.sleep(0.1)
            except Exception as exc:  # pragma: no cover - hardware dependent
                self._set_error(str(exc))
                time.sleep(2.0)


def format_value(value: Optional[float]) -> str:
    if value is None:
        return "--"
    return f"{value:.6g}"


def build_app(readers: List[MultimeterReader], window_seconds: int, refresh_ms: int) -> Dash:
    app = Dash(__name__)
    app.title = "Surface Conductivity Monitor"

    app.layout = html.Div(
        [
            html.H2("Surface Conductivity - Dual Multimeter Monitor"),
            html.P("Devices: Multicomp Pro MP730889 and MP730027"),
            html.Div(id="live-values", style={"fontSize": "1.2rem", "marginBottom": "1rem"}),
            html.Div(id="live-errors", style={"color": "#9f1d1d", "marginBottom": "1rem"}),
            dcc.Graph(id="live-graph"),
            dcc.Interval(id="tick", interval=refresh_ms, n_intervals=0),
        ],
        style={"maxWidth": "1100px", "margin": "0 auto", "padding": "1rem"},
    )

    @app.callback(
        Output("live-values", "children"),
        Output("live-errors", "children"),
        Output("live-graph", "figure"),
        Input("tick", "n_intervals"),
    )
    def update(_n: int):  # type: ignore[no-untyped-def]
        values_line = " | ".join(
            [f"{reader.name}: {format_value(reader.latest_value)}" for reader in readers]
        )

        errors = []
        for reader in readers:
            if reader.last_error:
                errors.append(f"{reader.name} ({reader.port}): {reader.last_error}")

        fig = go.Figure()
        for reader in readers:
            points = reader.get_points(window_seconds)
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=reader.name))

        fig.update_layout(
            title=f"Last {window_seconds} seconds",
            xaxis_title="Time",
            yaxis_title="Measured value",
            margin={"l": 40, "r": 20, "t": 50, "b": 40},
        )

        return values_line, " | ".join(errors), fig

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual USB multimeter live monitor")
    parser.add_argument("--port1", default="/dev/ttyUSB0", help="Serial port for MP730889")
    parser.add_argument("--port2", default="/dev/ttyUSB1", help="Serial port for MP730027")
    parser.add_argument("--baud1", type=int, default=9600, help="Baudrate for meter 1")
    parser.add_argument("--baud2", type=int, default=9600, help="Baudrate for meter 2")
    parser.add_argument("--query1", default="MEAS?", help="Query command for meter 1")
    parser.add_argument("--query2", default="MEAS?", help="Query command for meter 2")
    parser.add_argument("--window", type=int, default=60, help="Graph time window in seconds")
    parser.add_argument("--refresh-ms", type=int, default=500, help="UI refresh period in ms")
    parser.add_argument("--host", default="0.0.0.0", help="Web server host")
    parser.add_argument("--web-port", type=int, default=8050, help="Web server port")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run without hardware using generated demo signals",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    readers = [
        MultimeterReader(
            name="MP730889",
            port=args.port1,
            baudrate=args.baud1,
            query=args.query1,
            simulated=args.simulate,
        ),
        MultimeterReader(
            name="MP730027",
            port=args.port2,
            baudrate=args.baud2,
            query=args.query2,
            simulated=args.simulate,
        ),
    ]

    for reader in readers:
        reader.start()

    app = build_app(readers, window_seconds=args.window, refresh_ms=args.refresh_ms)
    app.run(host=args.host, port=args.web_port, debug=False)


if __name__ == "__main__":
    main()
