import logging
from datetime import timedelta

from django.utils import timezone

from .models import PersonalAccessToken

logger = logging.getLogger(__name__)


class TokenCleanupMiddleware:
    """Equivalent of Laravel TokenDestroy in the API middleware group."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            PersonalAccessToken.objects.filter(
                last_used_at__lte=timezone.now() - timedelta(hours=4)
            ).delete()
        except Exception:
            logger.exception("token cleanup failed")
        return self.get_response(request)


class AuditRequestMiddleware:
    """Adds request context to logs; business writes create Logs rows explicitly."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            logger.info("api_request method=%s path=%s status=%s ip=%s",
                        request.method, request.path, response.status_code,
                        request.META.get("REMOTE_ADDR", ""))
        return response
