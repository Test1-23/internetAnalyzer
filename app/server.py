import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .analyzer import Analyzer
from .connmon import ConnMon
from .dnsprobe import DnsProbe
from .geoip import GeoIP
from .monitor import NetworkMonitor
from .netinfo import NetInfo
from .pathmon import PathMonitor
from .storage import Storage
from .wifienv import WifiEnv

BASE = Path(__file__).resolve().parent
storage = Storage()
monitor = NetworkMonitor()
netinfo = NetInfo()
dnsprobe = DnsProbe(netinfo)
connmon = ConnMon(storage)
pathmon = PathMonitor(monitor, storage)
wifienv = WifiEnv(monitor)
geoip = GeoIP(storage)
analyzer = Analyzer(monitor, storage=storage, netinfo=netinfo,
                    dnsprobe=dnsprobe, connmon=connmon, pathmon=pathmon)


@asynccontextmanager
async def lifespan(app):
    monitor.start()
    netinfo.start()
    dnsprobe.start()
    connmon.start()
    pathmon.start()
    wifienv.start()
    geoip.start()
    analyzer.start()
    yield
    monitor.stop()
    netinfo.stop()
    dnsprobe.stop()
    connmon.stop()
    pathmon.stop()
    wifienv.stop()
    geoip.stop()
    analyzer.stop()


app = FastAPI(title="网络分析器", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/status")
def api_status():
    snap = dict(monitor.get_snapshot())
    ni = netinfo.get()
    snap["proxy"] = bool(ni.get("virtual_adapters")) or (ni.get("proxy") or {}).get("enabled")
    pub = ni.get("public_ip") or {}
    snap["public_ip"] = pub.get("ip")
    snap["isp"] = pub.get("isp")
    return snap


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
        "wifi_signal": [x.get("wifi_signal") for x in h],
    }


@app.get("/api/probes")
def api_probes(seconds: int = 600):
    return monitor.get_probes(min(max(seconds, 30), 900))


@app.get("/api/netinfo")
def api_netinfo():
    return netinfo.get()


@app.get("/api/dns-stats")
def api_dns_stats():
    return dnsprobe.get_stats()


@app.get("/api/traceroute")
def api_traceroute():
    return {"latest": pathmon.get_latest(),
            "history": storage.get_traces(60)}


@app.post("/api/traceroute/run")
async def api_trace_run():
    pathmon.trigger()
    return {"ok": True}


@app.get("/api/connections")
def api_connections():
    return {"processes": connmon.get_current(), "storms": connmon.get_storms()}


@app.get("/api/analysis")
def api_analysis():
    return analyzer.get_report()


@app.get("/api/wifi-env")
def api_wifi_env():
    return wifienv.get_state()


@app.post("/api/geo")
async def api_geo(ips: list[str]):
    return geoip.get(ips)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    last = 0
    try:
        while True:
            snap = monitor.get_snapshot()
            if snap and snap["ts"] != last:
                last = snap["ts"]
                payload = dict(snap)
                payload["cpu"] = monitor.cpu
                payload["mem"] = monitor.mem
                ni = netinfo.get()
                payload["proxy"] = bool(ni.get("virtual_adapters")) or (ni.get("proxy") or {}).get("enabled")
                pub = ni.get("public_ip") or {}
                payload["public_ip"] = pub.get("ip")
                payload["isp"] = pub.get("isp")
                await websocket.send_json(payload)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
