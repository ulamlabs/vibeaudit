from django.urls import path

from github_app.views import InstallationDeleteView, InstallationsView, ReposView, connect, setup

urlpatterns = [
    path("connect", connect, name="github_connect"),
    path("setup", setup, name="github_setup"),
    path("installations", InstallationsView.as_view(), name="github_installations"),
    path(
        "installations/<int:installation_id>",
        InstallationDeleteView.as_view(),
        name="github_installation_delete",
    ),
    path("repos", ReposView.as_view(), name="github_repos"),
]
