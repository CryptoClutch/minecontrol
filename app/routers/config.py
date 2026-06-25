from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import (
    FlightSheet, OCProfile, Rig, GPU, RigAssignment, GPUAssignment, Wallet,
    BoardFanProfile, WatchdogPolicy,
)
from app.schemas import (
    FlightSheetCreate, FlightSheetOut,
    OCProfileCreate, OCProfileOut,
    RigAssignmentSet, GPUAssignmentSet,
)

router = APIRouter(prefix="/api", tags=["config"])


# ---------- Flight sheets ----------

@router.post("/flight-sheets", response_model=FlightSheetOut)
def create_flight_sheet(payload: FlightSheetCreate, db: Session = Depends(get_db)):
    if db.query(FlightSheet).filter(FlightSheet.name == payload.name).first():
        raise HTTPException(400, f"Flight sheet '{payload.name}' already exists")
    if not db.get(Wallet, payload.wallet_id):
        raise HTTPException(404, f"Wallet id {payload.wallet_id} not found - create it via /api/wallets first")
    fs = FlightSheet(**payload.model_dump())
    db.add(fs)
    db.commit()
    db.refresh(fs)
    return fs


@router.get("/flight-sheets", response_model=list[FlightSheetOut])
def list_flight_sheets(db: Session = Depends(get_db)):
    return db.query(FlightSheet).all()


@router.put("/flight-sheets/{fs_id}", response_model=FlightSheetOut)
def update_flight_sheet(fs_id: int, payload: FlightSheetCreate, db: Session = Depends(get_db)):
    fs = db.get(FlightSheet, fs_id)
    if not fs:
        raise HTTPException(404, "Flight sheet not found")
    if not db.get(Wallet, payload.wallet_id):
        raise HTTPException(404, f"Wallet id {payload.wallet_id} not found")
    for k, v in payload.model_dump().items():
        setattr(fs, k, v)
    db.commit()
    db.refresh(fs)
    return fs


# ---------- OC profiles ----------

@router.post("/oc-profiles", response_model=OCProfileOut)
def create_oc_profile(payload: OCProfileCreate, db: Session = Depends(get_db)):
    if db.query(OCProfile).filter(OCProfile.name == payload.name).first():
        raise HTTPException(400, f"OC profile '{payload.name}' already exists")
    p = OCProfile(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("/oc-profiles", response_model=list[OCProfileOut])
def list_oc_profiles(db: Session = Depends(get_db)):
    return db.query(OCProfile).all()


@router.put("/oc-profiles/{profile_id}", response_model=OCProfileOut)
def update_oc_profile(profile_id: int, payload: OCProfileCreate, db: Session = Depends(get_db)):
    p = db.get(OCProfile, profile_id)
    if not p:
        raise HTTPException(404, "OC profile not found")
    for k, v in payload.model_dump().items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p


# ---------- Assignments ----------

@router.post("/assignments/rig")
def assign_flight_sheet(payload: RigAssignmentSet, db: Session = Depends(get_db)):
    rig = db.get(Rig, payload.rig_id)
    fs = db.get(FlightSheet, payload.flight_sheet_id)
    if not rig or not fs:
        raise HTTPException(404, "Rig or flight sheet not found")

    fan_profile = None
    if payload.board_fan_profile_id is not None:
        fan_profile = db.get(BoardFanProfile, payload.board_fan_profile_id)
        if not fan_profile:
            raise HTTPException(404, f"Board fan profile id {payload.board_fan_profile_id} not found")

    watchdog_policy = None
    if payload.watchdog_policy_id is not None:
        watchdog_policy = db.get(WatchdogPolicy, payload.watchdog_policy_id)
        if not watchdog_policy:
            raise HTTPException(404, f"Watchdog policy id {payload.watchdog_policy_id} not found")

    ra = db.query(RigAssignment).filter(RigAssignment.rig_id == rig.id).first()
    if not ra:
        ra = RigAssignment(rig_id=rig.id)
        db.add(ra)

    ra.flight_sheet_id = fs.id
    ra.board_fan_profile_id = payload.board_fan_profile_id
    ra.watchdog_policy_id = payload.watchdog_policy_id
    ra.status = "pending"  # agent will mark 'applied' on its next sync
    ra.applied_at = None
    db.commit()

    extras = []
    if fan_profile:
        extras.append(f"fan profile '{fan_profile.name}'")
    if watchdog_policy:
        extras.append(f"watchdog policy '{watchdog_policy.name}'")
    extra_msg = f" with {', '.join(extras)}" if extras else ""
    return {"status": "ok", "message": f"Rig '{rig.name}' assigned flight sheet '{fs.name}'{extra_msg}. Will apply on next agent sync."}


@router.post("/assignments/gpu")
def assign_oc_profile(payload: GPUAssignmentSet, db: Session = Depends(get_db)):
    gpu = db.get(GPU, payload.gpu_id)
    profile = db.get(OCProfile, payload.oc_profile_id)
    if not gpu or not profile:
        raise HTTPException(404, "GPU or OC profile not found")

    ga = db.query(GPUAssignment).filter(GPUAssignment.gpu_id == gpu.id).first()
    if not ga:
        ga = GPUAssignment(gpu_id=gpu.id)
        db.add(ga)

    ga.oc_profile_id = profile.id
    ga.status = "pending"
    ga.applied_at = None
    db.commit()
    return {"status": "ok", "message": f"GPU {gpu.gpu_index} on rig {gpu.rig_id} assigned OC profile '{profile.name}'. Will apply on next agent sync."}


@router.post("/assignments/rig/{rig_id}/confirm")
def confirm_rig_applied(rig_id: int, db: Session = Depends(get_db)):
    """Agent calls this after successfully applying the flight sheet."""
    ra = db.query(RigAssignment).filter(RigAssignment.rig_id == rig_id).first()
    if not ra:
        raise HTTPException(404, "No assignment found")
    ra.status = "applied"
    ra.applied_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}
