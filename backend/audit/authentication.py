from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import SessionAuthentication


class AuditAuthentication(SessionAuthentication):
    """Require an authenticated session unless anonymous audits are enabled."""

    def authenticate(self, request):
        if settings.ALLOW_ANONYMOUS_AUDIT:
            return None

        user_auth_tuple = super().authenticate(request)
        if user_auth_tuple is None:
            raise exceptions.NotAuthenticated("Authentication required")

        return user_auth_tuple