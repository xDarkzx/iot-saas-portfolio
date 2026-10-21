"""Curated excerpt from the private ManageMyFarm backend.

Source: cloud/backend/app/services/water_leak_detector.py
This excerpt depends on the larger application's SQLAlchemy models and policy.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.services.alert_policy import WATER_LEAK_POLICY
from app.tenant_models import FieldActivity, SensorReading

logger = logging.getLogger(__name__)


@dataclass
class LeakSignal:
    """One leak signal from one sensor type."""
    detector: str
    severity: str
    device_id: str
    metric: str
    value: float
    baseline: Optional[float]
    detected_at: datetime
    reason: str

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "detector": self.detector,
            "severity": self.severity,
            "metric": self.metric,
            "value": self.value,
            "baseline": self.baseline,
            "detected_at": self.detected_at.isoformat(),
            "reason": self.reason,
        }


def detect_tank_drop(
    tenant_db: Session,
    device_id: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[LeakSignal]:
    """Compare the latest water-level reading with the nearest reading to
    five minutes earlier. A policy threshold controls the rapid-drop alert.
    """
    pol = WATER_LEAK_POLICY
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=10)

    rows = (
        tenant_db.query(SensorReading.timestamp, SensorReading.water_level)
        .filter(SensorReading.device_id == device_id)
        .filter(SensorReading.water_level.isnot(None))
        .filter(SensorReading.timestamp >= cutoff)
        .order_by(SensorReading.timestamp.desc())
        .limit(20)
        .all()
    )
    if len(rows) < 2:
        return None

    latest_ts, latest_val = rows[0]
    if latest_val is None or latest_val <= 0:
        return None

    anchor_target = now - timedelta(minutes=5)
    anchor = min(rows[1:], key=lambda r: abs((r[0] - anchor_target).total_seconds()))
    anchor_ts, anchor_val = anchor
    if anchor_val is None or anchor_val <= 0:
        return None

    drop_pct = (anchor_val - latest_val) / anchor_val * 100.0
    if drop_pct < pol.tank_catastrophic_pct_5min:
        return None

    if _check_operator_irrigation(
        tenant_db,
        pol.operator_action_suppression_window_min,
        now,
    ):
        logger.debug(
            "leak_detector: tank_drop suppressed by active IRRIGATION "
            "activity (device=%s, drop=%.1f%%)",
            device_id,
            drop_pct,
        )
        return None

    return LeakSignal(
        detector="tank_drop",
        severity="catastrophic",
        device_id=device_id,
        metric="water_level",
        value=latest_val,
        baseline=anchor_val,
        detected_at=latest_ts if isinstance(latest_ts, datetime) else now,
        reason=(
            f"tank dropped {drop_pct:.1f}% in "
            f"{(latest_ts - anchor_ts).total_seconds()/60:.1f} min "
            f"({anchor_val:.1f} → {latest_val:.1f})"
        ),
    )


def _check_operator_irrigation(
    tenant_db: Session,
    window_min: int,
    now: datetime,
) -> bool:
    """Avoid treating a recent recorded irrigation activity as a leak."""
    cutoff = now - timedelta(minutes=window_min)
    query = (
        tenant_db.query(FieldActivity.id)
        .filter(FieldActivity.activity_type == "irrigation")
        .filter(
            (FieldActivity.status == "in_progress")
            | (
                (FieldActivity.status == "completed")
                & (FieldActivity.completed_date >= cutoff)
            )
        )
        .limit(1)
    )
    return query.first() is not None
