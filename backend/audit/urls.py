from django.urls import path

from audit.views import AuditSessionView, StartAuditView

urlpatterns = [
    path("start", StartAuditView.as_view(), name="audit_start"),
    path("session", AuditSessionView.as_view(), name="audit_session"),
]
