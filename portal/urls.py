from django.urls import path
from . import views

urlpatterns = [
    path("", views.login_view, name="portal-login"),
    path("logout/", views.logout_view, name="portal-logout"),
    path("dashboard/", views.dashboard, name="portal-dashboard"),

    path('dashboard/tasks/new/', views.task_create, name='task-create'),
    path('dashboard/tasks/<int:pk>/move/', views.task_move, name='task-move'),
    path('dashboard/tasks/<int:pk>/delete/', views.task_delete, name='task-delete'),
    path('dashboard/columns/new/', views.column_create, name='column-create'),
    path('dashboard/columns/<int:pk>/delete/', views.column_delete, name='column-delete'),
    path('dashboard/catalog/', views.catalog_view, name='portal-catalog'),
    path('dashboard/catalog/new/', views.process_type_create, name='process-type-create'),
    path('dashboard/catalog/<int:pk>/delete/', views.process_type_delete, name='process-type-delete'),
    path('dashboard/analytics/', views.analytics_view, name='portal-analytics'),

    # path('dashboard/tasks/<int:pk>/edit/', views.task_edit, name='task-edit'),
    # path('dashboard/tasks/<int:pk>/update/', views.task_update, name='task-update'),
    # path('dashboard/catalog/<int:pk>/edit/', views.column_edit, name='task-edit'),
    # path('dashboard/catalog/<int:pk>/update/', views.column_update, name='task-update'),
]