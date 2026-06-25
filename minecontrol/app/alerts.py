import os
import requests
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app.models.db import AlertLog, Rig

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Thresholds - adjust as needed, or move into DB-backed settings later
TEMP_WARN_C = 85
TEMP_CRITICAL_C = 90
FAN_STUCK_PCT = 35          # fan reporting below this while temp is high = suspected fault
HASHRATE_ZERO_GRACE_CHECKS = 1  # fire immediately if a GPU reports 0 hashrate while rig is online

# Don't re-fire the same alert type for the same GPU more often than this
ALERT_COOLDOWN_MINUTES = 15


def _recently_alerted(db: Session, rig_id: int, gpu_index: int, alert_type: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    existing = (
        db.query(AlertLog)
        .filter(
            AlertLog.rig_id == rig_id,
            AlertLog.gpu_index == gpu_index,
            AlertLog.alert_type == alert_type,
            AlertLog.timestamp >= cutoff,
        )
        .first()
    )
    return existing is not None


def send_discord(message: str):
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
        return resp.status_code in (200, 204)
    except requests.RequestException:
        return False


def _fire_alert(db: Session, rig: Rig, gpu_index: int, alert_type: str, message: str):
    if _recently_alerted(db, rig.id, gpu_index, alert_type):
        return  # cooldown active, skip

    sent = send_discord(f"**[{rig.name}]** {message}")
    log = AlertLog(
        rig_id=rig.id,
        gpu_index=gpu_index,
        alert_type=alert_type,
        message=message,
        discord_sent=sent,
    )
    db.add(log)
    db.commit()


def check_and_fire_alerts(db: Session, rig: Rig):
    """Run after every check-in: inspect each GPU's freshly-updated telemetry."""
    for gpu in rig.gpus:
        # Critical temperature
        if gpu.temp_c >= TEMP_CRITICAL_C:
            _fire_alert(
                db, rig, gpu.gpu_index, "temp_critical",
                f"GPU {gpu.gpu_index} ({gpu.name}) at {gpu.temp_c:.0f}°C — critical threshold ({TEMP_CRITICAL_C}°C) exceeded.",
            )
        elif gpu.temp_c >= TEMP_WARN_C:
            _fire_alert(
                db, rig, gpu.gpu_index, "temp_warning",
                f"GPU {gpu.gpu_index} ({gpu.name}) at {gpu.temp_c:.0f}°C — warning threshold ({TEMP_WARN_C}°C) exceeded.",
            )

        # Suspected fan fault: hot but fan not responding
        if gpu.temp_c >= TEMP_WARN_C and gpu.fan_pct <= FAN_STUCK_PCT:
            _fire_alert(
                db, rig, gpu.gpu_index, "fan_fault",
                f"GPU {gpu.gpu_index} ({gpu.name}) is hot ({gpu.temp_c:.0f}°C) but fan reports only {gpu.fan_pct:.0f}% — possible fan fault.",
            )

        # Zero hashrate while rig is online (and GPU isn't intentionally idle/unassigned)
        if gpu.hashrate <= 0:
            _fire_alert(
                db, rig, gpu.gpu_index, "hashrate_zero",
                f"GPU {gpu.gpu_index} ({gpu.name}) is reporting 0 hashrate while rig is online.",
            )
