"""
FastAPI entry point.

- GET /                 browser UI (Three.js)
- GET /api/layout       static city layout (roads, houses, junctions)
- GET /api/state        live snapshot (ego, traffic, pedestrians, signals, telemetry)
- GET /api/status       Zerobus forwarder status
- GET /healthz          health probe

The simulation and the telemetry forwarder run as background tasks.
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import config
from simulation import Simulation
from zerobus import ZerobusClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("car-sim")

BASE_DIR = Path(__file__).resolve().parent
sim = Simulation(config)
zerobus = ZerobusClient(config)
last_telemetry: dict | None = None


async def _sim_loop() -> None:
    """Run the simulation in fixed steps. Real time is accumulated and spent in whole
    steps, so timing jitter in this loop never changes how the vehicles move."""
    global last_telemetry
    step_s = 1.0 / config.sim_tick_hz
    accumulator = 0.0
    last = time.monotonic()
    while True:
        now = time.monotonic()
        if sim.mode == "menu":
            # The world is frozen on the menu: nothing is simulated, so nothing is sent.
            accumulator = 0.0
            last = now
            await asyncio.sleep(step_s / 2)
            continue
        accumulator = min(accumulator + (now - last), 0.25)   # never try to catch up more than 0.25 s
        last = now
        try:
            while accumulator >= step_s:
                for record in sim.step(step_s):
                    zerobus.enqueue(record)
                last_telemetry = sim.latest_telemetry
                accumulator -= step_s
        except Exception:
            log.exception("simulation step failed")
            accumulator = 0.0
        await asyncio.sleep(step_s / 2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [
        asyncio.create_task(_sim_loop()),
        asyncio.create_task(zerobus.run()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(title="Self-Driving Car Sim", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/layout")
async def layout():
    return sim.layout


@app.get("/api/state")
async def state():
    return sim.snapshot


@app.get("/api/stream")
async def stream():
    """Server-sent events: one message per simulation step (60 per second) on a single
    connection. The browser falls back to polling /api/state if this is unavailable."""
    async def events():
        last_t = sim.snapshot["t"]          # only send steps that happen after connecting
        interval = 0.5 / config.sim_tick_hz
        while True:
            snap = sim.snapshot
            if snap["t"] != last_t:
                last_t = snap["t"]
                yield "data: " + json.dumps(snap) + "\n\n"
            await asyncio.sleep(interval)
    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/status")
async def status():
    return {
        "zerobus": zerobus.status(),
        "telemetry_hz": config.telemetry_hz,
        "human_driver_seconds": config.human_driver_seconds,
        "last_telemetry": last_telemetry,
    }


@app.get("/healthz")
async def healthz():
    return {"ok": True, "sim_time_s": round(sim.t, 2)}


class HumanStart(BaseModel):
    reg: str
    name: str


class HumanInput(BaseModel):
    throttle: bool = False
    brake: bool = False
    left: bool = False
    right: bool = False


@app.post("/api/human/start")
async def human_start(body: HumanStart):
    reg = body.reg.strip().upper()
    name = body.name.strip()
    if not reg or len(reg) > 12:
        raise HTTPException(400, "Enter a vehicle registration (up to 12 characters).")
    if not name or len(name) > 60:
        raise HTTPException(400, "Enter a driver name (up to 60 characters).")
    try:
        sim.start_human(reg, name)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "limit_s": config.human_driver_seconds}


class ModeChoice(BaseModel):
    mode: str


@app.post("/api/mode")
async def set_mode(body: ModeChoice):
    try:
        sim.set_mode(body.mode)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "mode": sim.mode}


@app.post("/api/human/stop")
async def human_stop():
    sim.stop_human("stopped by driver")
    return {"ok": True}


@app.post("/api/human/input")
async def human_input(body: HumanInput):
    sim.set_human_input(body.model_dump())
    return {"ok": True}
