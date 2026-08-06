from django.urls import path
from . import views
from users import views as user_views

urlpatterns = [
    path("", views.login, name="portal-login"),
    path("dashboard/", views.dashboard, name="portal-dashboard"),
]