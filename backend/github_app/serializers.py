from rest_framework import serializers

from github_app.models import Installation


class InstallationSerializer(serializers.ModelSerializer):
    """Serializer for Installation model."""

    has_active_jobs = serializers.SerializerMethodField()

    class Meta:
        model = Installation
        fields = [
            "installation_id",
            "account_login",
            "account_type",
            "created_at",
            "remote_deleted_at",
            "has_active_jobs",
        ]

    def get_has_active_jobs(self, obj) -> bool:
        return self.context.get("has_active_jobs", False)


class RepoSerializer(serializers.Serializer):
    """Serializer for GitHub repositories."""

    id = serializers.IntegerField()
    full_name = serializers.CharField()
    private = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True)
