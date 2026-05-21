from django.urls import path

from audit.views import StartAuditView

urlpatterns = [
    path("start", StartAuditView.as_view(), name="audit_start"),
]
