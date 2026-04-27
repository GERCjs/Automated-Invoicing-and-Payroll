import logging

from .models import AuditLog

logger = logging.getLogger(__name__)


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_event(action, user=None, target_type="", target_id="", metadata=None, ip_address=None):
    try:
        AuditLog.objects.create(
            user=user,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            ip_address=ip_address,
        )
    except Exception:
        logger.exception("Failed to persist audit event '%s'.", action)
