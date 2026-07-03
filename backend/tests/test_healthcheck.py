from rest_framework.test import APIClient


def test_healthcheck_reports_build_metadata(settings) -> None:
    settings.APP_COMMIT = "abc123"
    settings.APP_IMAGE_TAG = "sha-abc123"

    response = APIClient().get("/api/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "commit": "abc123",
        "image_tag": "sha-abc123",
    }
