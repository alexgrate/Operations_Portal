from django.urls import path
from . import views

urlpatterns = [
    path("", views.login_view, name="portal-login"),
    path("logout/", views.logout_view, name="portal-logout"),
    path("dashboard/", views.dashboard, name="portal-dashboard"),
]