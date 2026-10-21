"""
Telemetry Service - Handles writing sensor data to the new telemetry table.

This service provides a clean interface for inserting telemetry data
in the narrow table format used by enterprise IoT platforms.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.tenant_models import Telemetry, METRIC_UNITS

logger = logging.getLogger(__name__)


def insert_telemetry(
    db: Session,
    device_id: str,
    timestamp: datetime,
    metrics: Dict[str, Any],
    quality: Optional[float] = None,
) -> int:
    """
    Insert sensor metrics into the telemetry table.

    Args:
        db: SQLAlchemy session for tenant database
        device_id: Device identifier (e.g., "FAFA000000009002")
        timestamp: Reading timestamp
        metrics: Dict of metric_name -> value (e.g., {"heart_rate_bpm": 65, "body_temperature_c": 38.5})
        quality: Optional signal quality (0-100)

    Returns:
        Number of rows inserted

    Example:
        insert_telemetry(
            db=tenant_db,
            device_id="FAFA000000009002",
            timestamp=datetime.now(timezone.utc),
            metrics={
                "heart_rate_bpm": 65,
                "body_temperature_c": 38.5,
                "activity_level": 42,
                "gps_latitude": -37.7801,
                "gps_longitude": 175.28,
            }
        )
    """
    rows_inserted = 0

    for metric_name, value in metrics.items():
        if value is None:
            continue  # Skip null values

        # Get unit from known metrics
        unit = METRIC_UNITS.get(metric_name, "")

        # Determine value type
        value_numeric = None
        value_string = None
        value_bool = None

        if isinstance(value, bool):
            value_bool = value
        elif isinstance(value, (int, float)):
            value_numeric = float(value)
        elif isinstance(value, str):
            # Try to parse as number
            try:
                value_numeric = float(value)
            except ValueError:
                value_string = str(value)[:256]  # Truncate to max length
        else:
            value_string = str(value)[:256]

        # Create telemetry row
        row = Telemetry(
            device_id=device_id,
            timestamp=timestamp,
            metric=metric_name,
            value_numeric=value_numeric,
            value_string=value_string,
            value_bool=value_bool,
            unit=unit,
            quality=quality,
        )
        db.add(row)
        rows_inserted += 1

    return rows_inserted


def extract_metrics_from_sensor_data(sensor_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract flat metrics from nested sensor_data structure.

    Handles the format from lorawan_handler where data comes as:
    {
        "sensor_data": {
            "heart_rate_bpm": 65,
            "gps_136": {"lat": -37.78, "lon": 175.28, "alt": 0}
        }
    }

    Returns flat dict like:
    {
        "heart_rate_bpm": 65,
        "gps_latitude": -37.78,
        "gps_longitude": 175.28,
        "gps_altitude": 0
    }
    """
    flat_metrics = {}

    for key, value in sensor_data.items():
        if value is None:
            continue

        # Handle nested GPS data
        if key.startswith("gps_") and isinstance(value, dict):
            if "lat" in value:
                flat_metrics["gps_latitude"] = value["lat"]
            if "lon" in value:
                flat_metrics["gps_longitude"] = value["lon"]
            if "alt" in value:
                flat_metrics["gps_altitude"] = value["alt"]
            continue

        # Handle nested location data
        if key == "location" and isinstance(value, dict):
            if "latitude" in value:
                flat_metrics["gps_latitude"] = value["latitude"]
            if "longitude" in value:
                flat_metrics["gps_longitude"] = value["longitude"]
            continue

        # Skip non-metric fields
        skip_fields = {
            "device_id", "timestamp", "raw_packet", "dev_eui", "gateway_eui",
            "f_port", "f_cnt", "rssi", "snr", "source", "raw_payload",
            "lora_address", "network_type", "packet_count", "uptime_seconds",
            "firmware_version", "tx_bytes", "rx_bytes"
        }
        if key in skip_fields:
            continue

        # Add scalar values directly
        if isinstance(value, (int, float, bool, str)):
            flat_metrics[key] = value

    return flat_metrics
