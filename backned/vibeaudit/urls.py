"""
URL configuration for vibeaudit project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.http import Http404
from django.template import TemplateDoesNotExist
from django.urls import path, re_path
from django.views.generic import TemplateView

from vibeaudit.views import healthcheck


class SafeTemplateView(TemplateView):
    """
    TemplateView that returns 404 instead of 500 when template doesn't exist
    """

    def get_template_names(self):
        try:
            return super().get_template_names()
        except Exception:
            raise Http404("Template not found")

    def get(self, request, *args, **kwargs):
        try:
            from django.template.loader import get_template

            template_name = self.get_template_names()[0]
            get_template(template_name)  # Test if template exists
            return super().get(request, *args, **kwargs)
        except TemplateDoesNotExist:
            raise Http404("Template not found")
        except Exception:
            raise Http404("Template not found")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/healthz", healthcheck, name="healthz"),
]

# Serve static files (WhiteNoise handles this efficiently in production)
# Always use the same static file serving approach
urlpatterns += staticfiles_urlpatterns()

# SPA fallback for BrowserRouter routes.
# Keep API and static assets out of the fallback.
urlpatterns += [
    re_path(
        r"^(?!api(?:/|$)|static(?:/|$)).*$",
        SafeTemplateView.as_view(template_name="index.html"),
    )
]
