from django.urls import path

from users import views as user_views

from . import views

urlpatterns = [
    path('', views.login_view, name='portal-login'),
    path('logout/', views.logout_view, name='portal-logout'),

    path('app/', views.home, name='portal-home'),
    path('app/q/<str:key>/', views.queue_view, name='queue'),

    path('app/tasks/new/', views.task_create, name='task-create'),
    path('app/tasks/<int:pk>/', views.task_detail, name='task-detail'),
    path('app/tasks/<int:pk>/update/', views.task_update, name='task-update'),
    path('app/tasks/<int:pk>/start/', views.task_start, name='task-start'),
    path('app/tasks/<int:pk>/checklist/', views.task_checklist, name='task-checklist'),
    path('app/tasks/<int:pk>/submit/', views.task_submit, name='task-submit'),
    path('app/tasks/<int:pk>/approve/', views.task_approve, name='task-approve'),
    path('app/tasks/<int:pk>/return/', views.task_return, name='task-return'),
    path('app/tasks/<int:pk>/archive/', views.task_archive, name='task-archive'),
    path('app/tasks/<int:pk>/comment/', views.comment_create, name='comment-create'),
    path('app/tasks/<int:pk>/attach/', views.attachment_upload, name='attachment-upload'),
    path('app/files/<int:pk>/', views.attachment_download, name='attachment-download'),
    path('app/files/<int:pk>/remove/', views.attachment_delete, name='attachment-delete'),

    path('app/catalog/', views.catalog_view, name='portal-catalog'),
    path('app/catalog/new/', views.process_type_create, name='process-type-create'),
    path('app/catalog/<int:pk>/update/', views.process_type_update, name='process-type-update'),
    path('app/catalog/<int:pk>/delete/', views.process_type_delete, name='process-type-delete'),
    path('app/catalog/<int:pk>/edit/', views.process_type_edit, name='process-type-edit'),

    path('app/analytics/', views.analytics_view, name='portal-analytics'),
    path('app/teams/', user_views.team_list, name='portal-teams'),
    path('app/teams/new/', user_views.team_create, name='team-create'),
    path('app/teams/<int:pk>/edit/', user_views.team_edit, name='team-edit'),
    path('app/teams/<int:pk>/toggle/', user_views.team_toggle, name='team-toggle'),
    path('app/staff/', views.staff_view, name='portal-staff'),
    path('app/staff/new/', user_views.staff_create, name='staff-create'),
    path('app/staff/<int:pk>/edit/', user_views.staff_edit, name='staff-edit'),
    path('app/staff/<int:pk>/resend/', user_views.staff_resend_invite, name='staff-resend'),
    path('app/staff/<int:pk>/toggle/', user_views.staff_deactivate, name='staff-toggle'),

    path('invite/<uidb64>/<token>/', user_views.InviteAcceptView.as_view(), name='invite-accept'),
]
