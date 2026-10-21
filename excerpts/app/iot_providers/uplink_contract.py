from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass
class UplinkData:
    """Standardized uplink data from device."""
    device_id: str
    dev_eui: Optional[str] = None
    gateway_eui: Optional[str] = None
    timestamp: Optional[str] = None
    rssi: Optional[int] = None
    snr: Optional[float] = None
    f_port: Optional[int] = None
    sensor_data: Optional[Dict[str, Any]] = None
    raw_payload: Optional[str] = None
    source: str = "unknown"  # "chirpstack", "aws_iot", etc.
