from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator


# ---------- Rigs ----------

class RigCreate(BaseModel):
    name: str
    lan_ip: str
    agent_type: str = "native"
    miner_api_port: int = 4068


class RigOut(BaseModel):
    id: int
    name: str
    lan_ip: str
    agent_type: str
    miner_api_port: int
    last_seen: Optional[datetime]
    status: str

    class Config:
        from_attributes = True


# ---------- GPU telemetry (reported by agents) ----------

class GPUReport(BaseModel):
    gpu_index: int
    name: Optional[str] = None
    hashrate: float = 0.0
    temp_c: float = 0.0
    fan_pct: float = 0.0
    power_draw_w: float = 0.0
    core_clock_mhz: float = 0.0
    mem_clock_mhz: float = 0.0
    shares_ok: int = 0
    shares_invalid: int = 0


class RigCheckIn(BaseModel):
    rig_name: str
    gpus: list[GPUReport]


class GPUOut(BaseModel):
    id: int
    rig_id: int
    gpu_index: int
    name: Optional[str]
    hashrate: float
    temp_c: float
    fan_pct: float
    power_draw_w: float
    core_clock_mhz: float
    mem_clock_mhz: float
    shares_ok: int
    shares_invalid: int
    last_updated: Optional[datetime]

    class Config:
        from_attributes = True


# ---------- Wallets ----------

class WalletCreate(BaseModel):
    name: str
    coin: str
    address: str
    notes: Optional[str] = None


class WalletOut(WalletCreate):
    id: int

    class Config:
        from_attributes = True


# ---------- Flight sheets ----------

KNOWN_MINERS = {"peakminer", "srbminer"}


class FlightSheetCreate(BaseModel):
    name: str
    coin: str
    miner: str = "peakminer"
    pool_url: str                  # may contain {wallet}, {worker}, {rig_name}
    wallet_id: int
    worker_name_template: str = "{rig_name}"
    extra_args: Optional[str] = None

    @field_validator("miner")
    @classmethod
    def validate_miner(cls, v):
        if v not in KNOWN_MINERS:
            raise ValueError(f"Unknown miner '{v}'. Supported: {', '.join(sorted(KNOWN_MINERS))}")
        return v


class FlightSheetOut(FlightSheetCreate):
    id: int

    class Config:
        from_attributes = True


# ---------- OC profiles ----------

class OCProfileCreate(BaseModel):
    name: str
    core_lock_mhz: Optional[int] = None
    mem_lock_mhz: Optional[int] = None
    core_offset_mhz: Optional[int] = None
    power_limit_w: Optional[int] = None
    fan_target_pct: Optional[int] = None
    notes: Optional[str] = None


class OCProfileOut(OCProfileCreate):
    id: int

    class Config:
        from_attributes = True


# ---------- Board fan profiles (whole-board, Octominer-style) ----------

class BoardFanProfileCreate(BaseModel):
    name: str
    mode: str = "auto"  # "auto" | "manual"
    min_fan_pct: Optional[int] = None
    max_fan_pct: Optional[int] = None
    target_core_temp_c: Optional[int] = None
    target_mem_temp_c: Optional[int] = None
    manual_fan_pct: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        if v not in ("auto", "manual"):
            raise ValueError("mode must be 'auto' or 'manual'")
        return v


class BoardFanProfileOut(BoardFanProfileCreate):
    id: int

    class Config:
        from_attributes = True


# ---------- Watchdog policies ----------

class WatchdogPolicyCreate(BaseModel):
    name: str
    check_interval_s: int = 120
    startup_grace_s: int = 300
    baseline_window_samples: int = 20
    global_hashrate_min_pct: Optional[int] = None
    per_gpu_hashrate_min_pct: Optional[int] = None
    notes: Optional[str] = None


class WatchdogPolicyOut(WatchdogPolicyCreate):
    id: int

    class Config:
        from_attributes = True


# ---------- Assignments ----------

class RigAssignmentSet(BaseModel):
    rig_id: int
    flight_sheet_id: int
    board_fan_profile_id: Optional[int] = None
    watchdog_policy_id: Optional[int] = None


class GPUAssignmentSet(BaseModel):
    gpu_id: int
    oc_profile_id: int


# ---------- Sync payload (what an agent pulls down) ----------

class GPULaunchConfig(BaseModel):
    gpu_index: int
    core_lock_mhz: Optional[int] = None
    mem_lock_mhz: Optional[int] = None
    core_offset_mhz: Optional[int] = None
    power_limit_w: Optional[int] = None
    fan_target_pct: Optional[int] = None


class BoardFanConfig(BaseModel):
    mode: str
    min_fan_pct: Optional[int] = None
    max_fan_pct: Optional[int] = None
    target_core_temp_c: Optional[int] = None
    target_mem_temp_c: Optional[int] = None
    manual_fan_pct: Optional[int] = None


class WatchdogConfig(BaseModel):
    check_interval_s: int
    startup_grace_s: int
    baseline_window_samples: int
    global_hashrate_min_pct: Optional[int] = None
    per_gpu_hashrate_min_pct: Optional[int] = None


class RigSyncConfig(BaseModel):
    rig_name: str
    coin: str
    miner: str
    pool_url: str
    wallet: str
    worker_name: str
    extra_args: Optional[str] = None
    gpus: list[GPULaunchConfig]
    board_fan: Optional[BoardFanConfig] = None
    watchdog: Optional[WatchdogConfig] = None
    pending_action: Optional[str] = None  # "restart" | "reboot" - set by the watchdog, agent should act and then clear it
