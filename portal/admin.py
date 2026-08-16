from django.contrib import admin

from .models import Approval, Comment, ProcessType, Task


@admin.register(ProcessType)
class ProcessTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'target_hours', 'approval_level', 'checklist_count']
    list_filter = ['approval_level']
    search_fields = ['name']

    @admin.display(description='Checklist items')
    def checklist_count(self, obj):
        return len(obj.checklist or [])


class ApprovalInline(admin.TabularInline):
    model = Approval
    extra = 0
    readonly_fields = ['actor', 'stage', 'decision', 'comment', 'created_at']
    can_delete = False


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    readonly_fields = ['author', 'created_at']


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'process_type', 'assignee', 'team', 'approval_stage', 'deadline']
    list_filter = ['approval_stage', 'process_type', 'team']
    search_fields = ['title']
    inlines = [ApprovalInline, CommentInline]
    readonly_fields = ['created_at', 'started_at', 'submitted_at', 'completed_at']


@admin.register(Approval)
class ApprovalAdmin(admin.ModelAdmin):
    """Read-only: the sign-off record is evidence, not something to edit."""
    list_display = ['task', 'stage', 'decision', 'actor', 'created_at']
    list_filter = ['stage', 'decision']
    readonly_fields = ['task', 'actor', 'stage', 'decision', 'comment', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['task', 'author', 'created_at']
    search_fields = ['body']
