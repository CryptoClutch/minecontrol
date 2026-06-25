from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import BoardFanProfile, WatchdogPolicy, RigAssignment
from app.schemas import (
    BoardFanProfileCreate, BoardFanProfileOut,
    WatchdogPolicyCreate, WatchdogPolicyOut,
)

router = APIRouter(prefix="/api", tags=["fan-and-watchdog"])


# ---------- Board fan profiles ----------

@router.post("/board-fan-profiles", response_model=BoardFanProfileOut)
def create_board_fan_profile(payload: BoardFanProfileCreate, db: Session = Depends(get_db)):
    if db.query(BoardFanProfile).filter(BoardFanProfile.name == payload.name).first():
        raise HTTPException(400, f"Board fan profile '{payload.name}' already exists")
    if payload.mode == "auto" and not all([
        payload.min_fan_pct, payload.max_fan_pct, payload.target_core_temp_c
    ]):
        raise HTTPException(400, "Auto mode requires min_fan_pct, max_fan_pct, and target_core_temp_c")
    if payload.mode == "manual" and payload.manual_fan_pct is None:
        raise HTTPException(400, "Manual mode requires manual_fan_pct")
    p = BoardFanProfile(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("/board-fan-profiles", response_model=list[BoardFanProfileOut])
def list_board_fan_profiles(db: Session = Depends(get_db)):
    return db.query(BoardFanProfile).all()


@router.put("/board-fan-profiles/{profile_id}", response_model=BoardFanProfileOut)
def update_board_fan_profile(profile_id: int, payload: BoardFanProfileCreate, db: Session = Depends(get_db)):
    p = db.get(BoardFanProfile, profile_id)
    if not p:
        raise HTTPException(404, "Board fan profile not found")
    for k, v in payload.model_dump().items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/board-fan-profiles/{profile_id}")
def delete_board_fan_profile(profile_id: int, db: Session = Depends(get_db)):
    p = db.get(BoardFanProfile, profile_id)
    if not p:
        raise HTTPException(404, "Board fan profile not found")
    in_use = db.query(RigAssignment).filter(RigAssignment.board_fan_profile_id == profile_id).count()
    if in_use:
        raise HTTPException(400, f"Cannot delete - {in_use} rig(s) still use this profile")
    db.delete(p)
    db.commit()
    return {"status": "ok"}


# ---------- Watchdog policies ----------

@router.post("/watchdog-policies", response_model=WatchdogPolicyOut)
def create_watchdog_policy(payload: WatchdogPolicyCreate, db: Session = Depends(get_db)):
    if db.query(WatchdogPolicy).filter(WatchdogPolicy.name == payload.name).first():
        raise HTTPException(400, f"Watchdog policy '{payload.name}' already exists")
    p = WatchdogPolicy(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("/watchdog-policies", response_model=list[WatchdogPolicyOut])
def list_watchdog_policies(db: Session = Depends(get_db)):
    return db.query(WatchdogPolicy).all()


@router.put("/watchdog-policies/{policy_id}", response_model=WatchdogPolicyOut)
def update_watchdog_policy(policy_id: int, payload: WatchdogPolicyCreate, db: Session = Depends(get_db)):
    p = db.get(WatchdogPolicy, policy_id)
    if not p:
        raise HTTPException(404, "Watchdog policy not found")
    for k, v in payload.model_dump().items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/watchdog-policies/{policy_id}")
def delete_watchdog_policy(policy_id: int, db: Session = Depends(get_db)):
    p = db.get(WatchdogPolicy, policy_id)
    if not p:
        raise HTTPException(404, "Watchdog policy not found")
    in_use = db.query(RigAssignment).filter(RigAssignment.watchdog_policy_id == policy_id).count()
    if in_use:
        raise HTTPException(400, f"Cannot delete - {in_use} rig(s) still use this policy")
    db.delete(p)
    db.commit()
    return {"status": "ok"}
