from rest_framework import serializers


class RepoSerializer(serializers.Serializer):
    """Serializer for GitHub repositories."""

    id = serializers.IntegerField()
    full_name = serializers.CharField()
    private = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True)
