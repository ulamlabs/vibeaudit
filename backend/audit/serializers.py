from rest_framework import serializers

from audit.models import AuditJob


class StartAuditSerializer(serializers.Serializer):
    """Serializer for starting an audit job."""

    repo_full_name = serializers.CharField(required=True)
    email = serializers.EmailField(required=True)


class AuditJobSerializer(serializers.ModelSerializer):
    """Serializer for AuditJob model."""

    class Meta:
        model = AuditJob
        fields = ["id", "repo_full_name", "email", "state", "created_at"]
        read_only_fields = ["id", "created_at", "state"]
