from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def healthcheck(request):
    return Response(
        {
            "status": "ok",
            "commit": settings.APP_COMMIT,
            "image_tag": settings.APP_IMAGE_TAG,
        }
    )
