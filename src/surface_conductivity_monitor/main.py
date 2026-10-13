from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import AppConfig, MeasurementMode, load_config
from .service import MeterMonitorService

BASE_DIR = Path(__file__).resolve().parent

class SetPointRequest(BaseModel):
    value: float


class SetOutputRequest(BaseModel):
    enabled: bool


class SetMeterModeRequest(BaseModel):
    mode: MeasurementMode


class ChooseRecordingFileRequest(BaseModel):
    current_path: str | None = None


class SetRecordingPathRequest(BaseModel):
    path: str


class StartRecordingRequest(BaseModel):
    overwrite: bool = False


def create_app(app_config: AppConfig | None = None) -> tuple[FastAPI, MeterMonitorService]:
    resolved_config = app_config or load_config()
    service = MeterMonitorService(resolved_config)
    app = FastAPI(title="Fermart Lab Data Logger")
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.on_event("startup")
    async def on_startup() -> None:
        await service.start()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await service.stop()

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(content=html)

    @app.get("/api/state")
    async def get_state() -> dict:
        return service.snapshot()

    @app.post("/api/meters/{meter_name}/mode")
    async def set_meter_mode(meter_name: str, body: SetMeterModeRequest) -> dict:
        try:
            return await service.set_meter_mode(meter_name, body.mode)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/power-source/voltage")
    async def set_power_source_voltage(body: SetPointRequest) -> dict:
        try:
            return await service.set_power_source_voltage(body.value)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/power-source/current")
    async def set_power_source_current(body: SetPointRequest) -> dict:
        try:
            return await service.set_power_source_current(body.value)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/power-source/output")
    async def set_power_source_output(body: SetOutputRequest) -> dict:
        try:
            return await service.set_power_source_output_enabled(body.enabled)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/recording/choose-file")
    async def choose_recording_file(body: ChooseRecordingFileRequest) -> dict:
        try:
            return await service.choose_recording_target(body.current_path)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/recording/path")
    async def set_recording_path(body: SetRecordingPathRequest) -> dict:
        try:
            return await service.set_recording_target(body.path)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/recording/start")
    async def start_recording(body: StartRecordingRequest) -> dict:
        try:
            return await service.start_recording(body.overwrite)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/recording/stop")
    async def stop_recording() -> dict:
        try:
            return await service.stop_recording()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app, service


config = load_config()
app, service = create_app(config)
