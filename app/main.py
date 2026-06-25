import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import init_db, get_db
from app.models.db import Rig
from app.routers import rigs, config, wallets, fan_watchdog
from app.ws_manager import manager

app = FastAPI(title="MineControl")

app.include_router(rigs.router)
app.include_router(config.router)
app.include_router(wallets.router)
app.include_router(fan_watchdog.router)


@app.on_event("startup")
def on_startup():
    init_db()
    asyncio.create_task(broadcast_loop())


async def broadcast_loop():
    """Every 5s, push current rig/GPU state to all connected dashboard clients."""
    from app.database import SessionLocal
    while True:
        await asyncio.sleep(5)
        db: Session = SessionLocal()
        try:
            rigs_data = []
            for rig in db.query(Rig).all():
                rigs_data.append({
                    "id": rig.id,
                    "name": rig.name,
                    "status": rig.status,
                    "last_seen": rig.last_seen.isoformat() if rig.last_seen else None,
                    "gpus": [
                        {
                            "gpu_index": g.gpu_index,
                            "name": g.name,
                            "hashrate": g.hashrate,
                            "temp_c": g.temp_c,
                            "fan_pct": g.fan_pct,
                            "power_draw_w": g.power_draw_w,
                            "core_clock_mhz": g.core_clock_mhz,
                            "mem_clock_mhz": g.mem_clock_mhz,
                            "shares_ok": g.shares_ok,
                            "shares_invalid": g.shares_invalid,
                        }
                        for g in rig.gpus
                    ],
                })
            await manager.broadcast({"type": "snapshot", "rigs": rigs_data})
        finally:
            db.close()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # we don't expect client messages, just keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


# Serve the dashboard (static SPA) at /
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def serve_dashboard():
    return FileResponse("app/static/index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}
