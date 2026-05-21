from django.urls import path

from github_app.views import ReposView, connect, setup

urlpatterns = [
    path("connect", connect, name="github_connect"),
    path("setup", setup, name="github_setup"),
    path("repos", ReposView.as_view(), name="github_repos"),
]
