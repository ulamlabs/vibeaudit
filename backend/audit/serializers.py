from rest_framework import serializers

from audit.models import AuditJob


class StartAuditSerializer(serializers.Serializer):
    """Serializer for starting an audit job."""

    repo_full_name = serializers.CharField(required=True)
    email = serializers.EmailField(required=True)

    def validate_repo_full_name(self, value: str) -> str:
        repo_full_name = value.strip()
        owner, separator, repo = repo_full_name.partition("/")
        if not separator or not owner or not repo or "/" in repo:
            raise serializers.ValidationError(
                "Repository must be in the format 'owner/repo'."
            )
        return repo_full_name


class AuditJobSerializer(serializers.ModelSerializer):
    """Serializer for AuditJob model."""

    class Meta:
        model = AuditJob
        fields = ["id", "repo_full_name", "email", "state", "created_at"]
        read_only_fields = ["id", "created_at", "state"]
