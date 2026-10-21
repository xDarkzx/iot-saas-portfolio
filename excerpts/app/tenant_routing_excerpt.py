import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func

from app.database import get_platform_db_context
from app.models import BaseStation, Tenant

logger = logging.getLogger("lorawan_handler")


def get_tenant_by_gateway_eui(gateway_eui: str) -> Optional[str]:
    """
    Lookup tenant database name by gateway EUI.
    Returns tenant db_name if found, None otherwise.
    """
    if not gateway_eui:
        return None

    try:
        with get_platform_db_context() as db:
            # Find gateway by EUI (Check both serial_number and gateway_eui columns)
            # Some registration flows use serial_number, others use gateway_eui
            gateway = db.query(BaseStation).filter(
                (func.upper(BaseStation.serial_number) == gateway_eui.upper()) |
                (func.upper(BaseStation.gateway_eui) == gateway_eui.upper())
            ).first()

            if gateway:
                # Update gateway status
                gateway.status = "online"
                gateway.last_seen = datetime.now(timezone.utc)
                db.commit()

                # Get tenant
                tenant = db.query(Tenant).filter(
                    Tenant.id == gateway.tenant_id
                ).first()

                if tenant:
                    logger.info(f"📡 Gateway {gateway_eui} -> Tenant: {tenant.name}")
                    return tenant.db_name

            logger.warning(f"Gateway {gateway_eui} not claimed by any tenant")
            return None

    except Exception as e:
        logger.error(f"Error looking up gateway: {e}")
        return None
