from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import SessionAuthentication


class AuditAuthentication(SessionAuthentication):
    """Enforce session authentication unless unauthenticated audits are allowed."""

    def authenticate(self, request):
        user_auth_tuple = super().authenticate(request)
        if user_auth_tuple is None and not settings.ALLOW_UNAUTHENTICATED_AUDIT:
            raise exceptions.NotAuthenticated("Authentication required")
        return user_auth_tuple
