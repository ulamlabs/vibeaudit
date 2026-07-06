from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import SessionAuthentication


class AuditAuthentication(SessionAuthentication):
    """Session auth for the audit funnel. CSRF enforcement is opt-in via AUDIT_ENFORCE_CSRF."""

    def enforce_csrf(self, request):
        if settings.AUDIT_ENFORCE_CSRF:
            super().enforce_csrf(request)

    def authenticate(self, request):
        user_auth_tuple = super().authenticate(request)
        if user_auth_tuple is None and not settings.ALLOW_UNAUTHENTICATED_AUDIT:
            raise exceptions.NotAuthenticated("Authentication required")
        return user_auth_tuple
