"""
AWS IoT Core Provider.
Implements the IoTProvider interface using AWS IoT Core & Wireless.
Refactored to delegate to modular managers (Device/Gateway/Cert).
"""
import logging
import boto3
import json
import base64
from typing import Dict, Any, Optional, List, Callable

from .base import IoTProvider, GatewayInfo, DeviceInfo, UplinkData
from ..config import settings
from .aws.gateway_manager import AWSGatewayManager
from .aws.device_manager import AWSDeviceManager

logger = logging.getLogger("iot_providers.aws_iot")

class AWSIoTProvider(IoTProvider):
    """
    AWS IoT Core provider.
    Coordinates MQTT connection and delegates management to sub-managers.
    """
    def __init__(self):
        self._uplink_handler: Optional[Callable[[UplinkData], None]] = None
        self._join_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        self._mqtt_connection = None
        self._iot_client = None
        self._wireless_client = None
        self._connected = False

        # Managers (Initialized in start())
        self.gateways: Optional[AWSGatewayManager] = None
        self.devices: Optional[AWSDeviceManager] = None

    async def start(self) -> None:
        """Initialize AWS IoT connections and managers."""
        if not settings.aws_iot_endpoint:
            logger.info("AWS IoT Core not configured (no AWS_IOT_ENDPOINT).")
            return

        try:
            # 1. AWS Clients
            self._iot_client = boto3.client(
                "iot", region_name=settings.aws_iot_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
            self._wireless_client = boto3.client(
                "iotwireless", region_name=settings.aws_iot_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )

            # 2. Initialize Managers
            self.gateways = AWSGatewayManager(self._iot_client, self._wireless_client)
            self.devices = AWSDeviceManager(self._iot_client, self._wireless_client)

            # 3. MQTT Connection
            if settings.aws_iot_cert_path and settings.aws_iot_key_path:
                self._connect_mqtt()

            # 4. Verify Connection
            self._iot_client.describe_endpoint(endpointType="iot:Data-ATS")
            self._connected = True
            logger.info("✅ AWS IoT Core provider started (Refactored)")

        except Exception as e:
            logger.error(f"Failed to start AWS IoT provider: {e}")
            self._connected = False

    def _connect_mqtt(self):
        """Establish MQTT connection and subscribe to LoRaWAN topics."""
        try:
            from awsiot import mqtt_connection_builder
            from awscrt import mqtt
            import uuid

            self._mqtt_connection = mqtt_connection_builder.mtls_from_path(
                endpoint=settings.aws_iot_endpoint,
                cert_filepath=settings.aws_iot_cert_path,
                pri_key_filepath=settings.aws_iot_key_path,
                ca_filepath=settings.aws_iot_root_ca_path,
                client_id=f"myfarmeye-backend-{uuid.uuid4().hex[:8]}",
            )
            self._mqtt_connection.connect().result()
            logger.info("✅ AWS IoT MQTT connected")

            # Only subscribe to AWS format now (ChirpStack removed)
            topic = "myfarmeye/lorawan/+"
            self._mqtt_connection.subscribe(
                topic=topic, qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_message_received
            )
            logger.info(f"Subscribed to: {topic}")

        except Exception as e:
            logger.warning(f"AWS IoT MQTT failure: {e}")

    def _on_message_received(self, topic, payload, dup, qos, retain, **kwargs):
        """Handle incoming MQTT messages (AWS Wireless format)."""
        try:
            payload_str = payload.decode("utf-8")
            data = json.loads(payload_str)

            # Handle AWS IoT Wireless Format
            # Topic: myfarmeye/lorawan/{WirelessDeviceId}
            if "PayloadData" in data and "WirelessMetadata" in data:
                if self._uplink_handler:
                    meta = data.get("WirelessMetadata", {}).get("LoRaWAN", {})
                    gws = meta.get("Gateways", [])

                    # Decode payload
                    raw = data.get("PayloadData", "")
                    try: payload_hex = base64.b64decode(raw).hex()
                    except Exception: payload_hex = raw

                    dev_eui = meta.get("DevEui", "").lower()

                    uplink = UplinkData(
                        device_id=dev_eui,
                        dev_eui=dev_eui,
                        gateway_eui=gws[0].get("GatewayEui", "") if gws else None,
                        timestamp=meta.get("Timestamp"),
                        rssi=gws[0].get("Rssi") if gws else None,
                        snr=gws[0].get("Snr") if gws else None,
                        f_port=meta.get("FPort", 1),
                        raw_payload=payload_hex,
                        source="aws_iot"
                    )
                    logger.info(f"⚡ Uplink from {dev_eui}")
                    self._uplink_handler(uplink)

        except Exception as e:
            logger.error(f"MQTT Error: {e}")

    async def stop(self) -> None:
        if self._mqtt_connection:
             try: self._mqtt_connection.disconnect().result()
             except Exception as e: logger.debug(f"MQTT disconnect cleanup: {e}")
        self._connected = False
        logger.info("AWS IoT Core provider stopped")

    def is_connected(self) -> bool: return self._connected

    # ==================== DELEGATED GATEWAY METHODS ====================

    async def register_gateway(self, tenant_name: str, gateway_eui: str, name: str, description: str = "", location: Optional[Dict[str, float]] = None) -> bool:
        return await self.gateways.register_gateway(tenant_name, gateway_eui, name, description, location) if self.gateways else False

    async def list_gateways(self, tenant_name: str, limit: int = 100) -> List[GatewayInfo]:
        return await self.gateways.list_gateways(tenant_name) if self.gateways else []

    async def get_gateway(self, gateway_eui: str) -> Optional[GatewayInfo]:
        return await self.gateways.get_gateway(gateway_eui) if self.gateways else None

    async def delete_gateway(self, gateway_eui: str) -> bool:
        return await self.gateways.delete_gateway(gateway_eui) if self.gateways else False

    async def ensure_gateway_ready(self, gateway_eui: str) -> bool:
        # Pass Through
        if not self._wireless_client: return False
        thing_name = f"gateway-{gateway_eui}"
        # Need wireless ID
        try:
             resp = self._wireless_client.get_wireless_gateway(Identifier=gateway_eui, IdentifierType="GatewayEui")
             w_id = resp.get("Id")
             from .aws.cert_manager import AWSCertManager
             cm = AWSCertManager(self._iot_client, self._wireless_client)
             return cm.ensure_cert_ready(gateway_eui, w_id, thing_name)
        except Exception: return False

    async def create_gateway_cert(self, eui: str) -> Dict[str, str]:
        # Only used by manual API, calling into cert manager
        # Simplified: ensure_gateway_ready does the heavy lifting now.
        return {}

    async def update_gateway_location(self, gateway_eui: str, latitude: float, longitude: float) -> bool:
         # TODO: Add to manager
         if self._iot_client:
             try:
                 self._iot_client.update_thing(thingName=f"gateway-{gateway_eui}", attributePayload={"attributes": {"latitude": str(latitude), "longitude": str(longitude)}, "merge": True})
                 return True
             except Exception as e:
                 logger.warning(f"Failed to update gateway location for {gateway_eui}: {e}")
         return False

    # ==================== DELEGATED DEVICE METHODS ====================

    async def register_device(self, tenant_name: str, device_id: str, device_name: str, device_type: str, app_key: Optional[str] = None, device_profile: Optional[str] = None, **kwargs) -> bool:
        return await self.devices.register_device(tenant_name, device_id, device_name, device_type, app_key, device_profile) if self.devices else False

    async def list_devices(self, tenant_name: str, limit: int = 100) -> List[DeviceInfo]:
        return await self.devices.list_devices(tenant_name) if self.devices else []

    async def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        return await self.devices.get_device(device_id) if self.devices else None

    async def delete_device(self, device_id: str) -> bool:
        return await self.devices.delete_device(device_id) if self.devices else False

    async def send_downlink(self, device_id: str, payload: bytes, f_port: int = 1, confirmed: bool = False, device_class: str = "A") -> bool:
        return await self.devices.send_downlink(device_id, payload, f_port, confirmed) if self.devices else False

    # ==================== HANDLERS ====================
    def set_uplink_handler(self, handler: Callable[[UplinkData], None]) -> None: self._uplink_handler = handler
    def set_join_handler(self, handler: Callable[[Dict[str, Any]], None]) -> None: self._join_handler = handler
    @property
    def provider_name(self) -> str: return "aws_iot"
    @property
    def capabilities(self) -> Dict[str, bool]: return {"lorawan": True, "mqtt": True, "downlink": True}
