import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .analyzer import Analyzer
from .monitor import NetworkMonitor

BASE = Path(__file__).resolve().parent
monitor = NetworkMonitor()
analyzer = Analyzer(monitor)


@asynccontextmanager
async def lifespan(app):
    monitor.start()
    analyzer.start()
    yield
    monitor.stop()
    analyzer.stop()


app = FastAPI(title="网络分析器", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/status")
def api_status():
    return monitor.get_snapshot()


@app.get("/api/history")
def api_history(seconds: int = 3600):
    seconds = min(max(seconds, 30), 21600)
    h = monitor.get_history(seconds)
    return {
        "ts": [x["ts"] for x in h],
        "status": [x["status"] for x in h],
        "latency": [x["latency"] for x in h],
        "jitter": [x["jitter"] for x in h],
        "loss_pct": [x["loss_pct"] for x in h],
        "down_bps": [x["down_bps"] for x in h],
        "up_bps": [x["up_bps"] for x in h],
    }


@app.get("/api/probes")
def api_probes(seconds: int = 600):
    return monitor.get_probes(min(max(seconds, 30), 900))


@app.get("/api/analysis")
def api_analysis():
    return analyzer.get_report()


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    last = 0
    try:
        while True:
            snap = monitor.get_snapshot()
            if snap and snap["ts"] != last:
                last = snap["ts"]
                await websocket.send_json(snap)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
