from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import Rig, GPU, RigAssignment, GPUAssignment
from app.schemas import (
    RigCreate, RigOut, RigCheckIn, RigSyncConfig, GPULaunchConfig,
    BoardFanConfig, WatchdogConfig,
)
from app.alerts import check_and_fire_alerts
from app.watchdog import check_watchdog
from app.safe_template import safe_format

router = APIRouter(prefix="/api/rigs", tags=["rigs"])


@router.post("", response_model=RigOut)
def create_rig(payload: RigCreate, db: Session = Depends(get_db)):
    existing = db.query(Rig).filter(Rig.name == payload.name).first()
    if existing:
        raise HTTPException(400, f"Rig '{payload.name}' already exists")
    rig = Rig(**payload.model_dump())
    db.add(rig)
    db.commit()
    db.refresh(rig)
    return rig


@router.get("", response_model=list[RigOut])
def list_rigs(db: Session = Depends(get_db)):
    return db.query(Rig).all()


@router.get("/{rig_id}", response_model=RigOut)
def get_rig(rig_id: int, db: Session = Depends(get_db)):
    rig = db.get(Rig, rig_id)
    if not rig:
        raise HTTPException(404, "Rig not found")
    return rig


@router.post("/checkin")
def rig_checkin(payload: RigCheckIn, db: Session = Depends(get_db)):
    """
    Called by each rig's agent on a regular interval (e.g. every 30s).
    Updates rig 'last_seen'/status and upserts per-GPU telemetry.
    Triggers alert checks (temp, offline-recovery, hashrate) after updating.
    """
    rig = db.query(Rig).filter(Rig.name == payload.rig_name).first()
    if not rig:
        raise HTTPException(404, f"Unknown rig '{payload.rig_name}' - register it first")

    rig.last_seen = datetime.now(timezone.utc)
    rig.status = "online"

    for g in payload.gpus:
        gpu = (
            db.query(GPU)
            .filter(GPU.rig_id == rig.id, GPU.gpu_index == g.gpu_index)
            .first()
        )
        if not gpu:
            gpu = GPU(rig_id=rig.id, gpu_index=g.gpu_index)
            db.add(gpu)

        gpu.name = g.name or gpu.name
        gpu.hashrate = g.hashrate
        gpu.temp_c = g.temp_c
        gpu.fan_pct = g.fan_pct
        gpu.power_draw_w = g.power_draw_w
        gpu.core_clock_mhz = g.core_clock_mhz
        gpu.mem_clock_mhz = g.mem_clock_mhz
        gpu.shares_ok = g.shares_ok
        gpu.shares_invalid = g.shares_invalid
        gpu.last_updated = datetime.now(timezone.utc)

    db.commit()

    # fire any temp/fan/hashrate alerts based on the data we just stored
    check_and_fire_alerts(db, rig)

    # evaluate the hashrate watchdog (rolling baseline + restart/reboot ladder)
    check_watchdog(db, rig)

    return {"status": "ok"}


@router.get("/{rig_id}/sync", response_model=RigSyncConfig)
def get_sync_config(rig_id: int, db: Session = Depends(get_db)):
    """
    Called by an agent to pull its current desired config:
    flight sheet (coin/pool/wallet) + each GPU's assigned OC profile.
    This is what the agent uses to build the actual miner launch command.

    Template variables supported in pool_url and worker_name_template,
    HiveOS-style:
      {wallet}    -> resolved address from the flight sheet's linked Wallet
      {rig_name}  -> this rig's name
      {worker}    -> the resolved worker name (usually == rig_name, but
                     kept distinct in case you template it differently)
    """
    rig = db.get(Rig, rig_id)
    if not rig:
        raise HTTPException(404, "Rig not found")

    ra = db.query(RigAssignment).filter(RigAssignment.rig_id == rig.id).first()
    if not ra:
        raise HTTPException(409, f"No flight sheet assigned to rig '{rig.name}' yet")

    fs = ra.flight_sheet
    wallet = fs.wallet
    if not wallet:
        raise HTTPException(409, f"Flight sheet '{fs.name}' has no linked wallet")

    worker_name = safe_format(fs.worker_name_template, {"rig_name": rig.name, "wallet": wallet.address})
    resolved_pool_url = safe_format(fs.pool_url, {"rig_name": rig.name, "worker": worker_name, "wallet": wallet.address})

    gpu_configs: list[GPULaunchConfig] = []
    for gpu in rig.gpus:
        assignment = (
            db.query(GPUAssignment).filter(GPUAssignment.gpu_id == gpu.id).first()
        )
        if assignment and assignment.oc_profile:
            p = assignment.oc_profile
            gpu_configs.append(GPULaunchConfig(
                gpu_index=gpu.gpu_index,
                core_lock_mhz=p.core_lock_mhz,
                mem_lock_mhz=p.mem_lock_mhz,
                core_offset_mhz=p.core_offset_mhz,
                power_limit_w=p.power_limit_w,
                fan_target_pct=p.fan_target_pct,
            ))
        else:
            # no profile assigned - agent should leave this GPU at defaults
            gpu_configs.append(GPULaunchConfig(gpu_index=gpu.gpu_index))

    board_fan = None
    if ra.board_fan_profile:
        bf = ra.board_fan_profile
        board_fan = BoardFanConfig(
            mode=bf.mode,
            min_fan_pct=bf.min_fan_pct,
            max_fan_pct=bf.max_fan_pct,
            target_core_temp_c=bf.target_core_temp_c,
            target_mem_temp_c=bf.target_mem_temp_c,
            manual_fan_pct=bf.manual_fan_pct,
        )

    watchdog = None
    if ra.watchdog_policy:
        wp = ra.watchdog_policy
        watchdog = WatchdogConfig(
            check_interval_s=wp.check_interval_s,
            startup_grace_s=wp.startup_grace_s,
            baseline_window_samples=wp.baseline_window_samples,
            global_hashrate_min_pct=wp.global_hashrate_min_pct,
            per_gpu_hashrate_min_pct=wp.per_gpu_hashrate_min_pct,
        )

    return RigSyncConfig(
        rig_name=rig.name,
        coin=fs.coin,
        miner=fs.miner,
        pool_url=resolved_pool_url,
        wallet=wallet.address,
        worker_name=worker_name,
        extra_args=fs.extra_args,
        gpus=gpu_configs,
        board_fan=board_fan,
        watchdog=watchdog,
        pending_action=ra.pending_action,
    )


@router.post("/{rig_id}/clear-pending-action")
def clear_pending_action(rig_id: int, db: Session = Depends(get_db)):
    """Agent calls this after it has acted on a watchdog-triggered restart/reboot."""
    ra = db.query(RigAssignment).filter(RigAssignment.rig_id == rig_id).first()
    if not ra:
        raise HTTPException(404, "No assignment found")
    ra.pending_action = None
    db.commit()
    return {"status": "ok"}
