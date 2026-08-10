from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import ColumnForm, ProcessTypeForm, TaskForm, CommentForm
from .models import Column, ProcessType, Task, Comment


# --- Authentication ---------------------------------------------------------

def login_view(request):
    if request.user.is_authenticated:
        return redirect('portal-dashboard')

    email = ''

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        if not email or not password:
            messages.error(request, 'Please enter both your email and your password.')
        else:
            user = authenticate(request, username=email, password=password)
            if user is None:
                messages.error(request, 'That email and password combination is not recognised.')
            else:
                auth_login(request, user)
                messages.success(request, f'Welcome back, {user.get_short_name() or user.get_username()}.')
                return redirect(_safe_next(request) or reverse('portal-dashboard'))

    return render(request, 'users/login.html', {
        'email': email,
        'google_login_enabled': settings.GOOGLE_LOGIN_ENABLED,
    })


def _safe_next(request):
    """Return ?next= only when it points back at this site."""
    target = request.POST.get('next') or request.GET.get('next')
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return None


@require_POST
def logout_view(request):
    auth_logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('portal-login')

APPROVER_GROUPS = ('Admin', 'Team Lead')


def can_approve(user):
    return bool(
        user.is_authenticated
        and (user.is_staff or user.is_superuser
             or user.groups.filter(name__in=APPROVER_GROUPS).exists())
    )


def apply_status_transition(task):
    """Set the timestamps that follow from the task's current column.

    Shared by create, move, update and approve so a task can't end up in a
    different state depending on which control the user happened to use.
    """
    status = task.status

    if status.starts and not task.started_at:
        task.started_at = timezone.now()

    if status.completes:
        if task.process_type.requires_approval and not task.approved_at:
            task.awaiting_approval = True
            task.completed_at = None
        else:
            task.awaiting_approval = False
            if not task.completed_at:
                task.completed_at = timezone.now()
    else:
        task.awaiting_approval = False
        task.completed_at = None
        task.approved_at = None
        task.approved_by = None


def compute_deadline_status(task):
    """Where a task sits against its process type's turnaround target."""
    if task.completed_at:
        return 'done'
    if task.awaiting_approval:
        return 'awaiting'

    target_seconds = task.process_type.target_hours * 3600
    if target_seconds <= 0:
        return 'overdue'

    pct = (timezone.now() - task.created_at).total_seconds() / target_seconds
    if pct >= 1:
        return 'overdue'
    if pct >= 0.7:
        return 'at-risk'
    return 'on-track'

def admin_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role == 'admin'):
            messages.error(request, 'Only Admins can do that.')
            return redirect('portal-dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper

def _clean_id(value):
    """Accept a filter/lookup id from a query string only if it's a number."""
    return value if (value or '').isdigit() else ''


def _dashboard_context(request, task_form=None, column_form=None):
    columns = list(Column.objects.all())
    tasks = Task.objects.select_related('process_type', 'status', 'assignee')

    process_filter = _clean_id(request.GET.get('process'))
    assignee_filter = _clean_id(request.GET.get('assignee'))
    if process_filter:
        tasks = tasks.filter(process_type_id=process_filter)
    if assignee_filter:
        tasks = tasks.filter(assignee_id=assignee_filter)
    tasks = list(tasks)

    columns_with_tasks = []
    for column in columns:
        column_tasks = [t for t in tasks if t.status_id == column.id]
        for task in column_tasks:
            task.deadline_status = compute_deadline_status(task)
        columns_with_tasks.append({'column': column, 'tasks': column_tasks})

    return {
        'active_tab': 'board',
        'can_approve': can_approve(request.user),
        'columns_with_tasks': columns_with_tasks,
        'all_columns': columns,
        'process_types': ProcessType.objects.all(),
        'staff': User.objects.all(),
        'task_form': task_form if task_form is not None else TaskForm(),
        'column_form': column_form if column_form is not None else ColumnForm(),
        'selected_process': process_filter,
        'selected_assignee': assignee_filter,
    }


def _catalog_context(form=None):
    return {
        'active_tab': 'catalog',
        'process_types': ProcessType.objects.all(),
        'form': form if form is not None else ProcessTypeForm(),
    }


# --- Task board -------------------------------------------------------------

@login_required
def dashboard(request):
    return render(request, 'portal/dashboard.html', _dashboard_context(request))


@login_required
@require_POST
def task_create(request):
    form = TaskForm(request.POST)
    if form.is_valid():
        task = form.save(commit=False)
        apply_status_transition(task)
        task.save()
        messages.success(request, 'Task created.')
        return redirect('portal-dashboard')

    messages.error(request, 'Could not create the task — check the highlighted fields.')
    context = _dashboard_context(request, task_form=form)
    context['open_task_modal'] = True
    return render(request, 'portal/dashboard.html', context)


@login_required
@require_POST
def task_move(request, pk):
    task = get_object_or_404(Task, pk=pk)

    status_id = _clean_id(request.POST.get('status'))
    if not status_id:
        messages.error(request, 'Pick a column to move the task to.')
        return redirect('portal-dashboard')

    task.status = get_object_or_404(Column, pk=status_id)
    apply_status_transition(task)
    task.save()

    if task.awaiting_approval:
        messages.info(request, f'“{task.title}” is now waiting for approval.')
    return redirect('portal-dashboard')


@login_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk)
    task.delete()
    messages.success(request, 'Task deleted.')
    return redirect('portal-dashboard')


@login_required
@admin_required
@require_POST
def column_create(request):
    form = ColumnForm(request.POST)
    if form.is_valid():
        column = form.save(commit=False)
        highest = Column.objects.aggregate(Max('order'))['order__max'] or 0
        column.order = highest + 1
        column.save()
        messages.success(request, 'Column added.')
        return redirect('portal-dashboard')

    messages.error(request, 'Could not add the column — check the highlighted fields.')
    context = _dashboard_context(request, column_form=form)
    context['open_column_modal'] = True
    return render(request, 'portal/dashboard.html', context)


@login_required
@admin_required
@require_POST
def column_delete(request, pk):
    column = get_object_or_404(Column, pk=pk)
    if column.tasks.exists():
        messages.error(request, 'Move or delete the tasks in this column first.')
    else:
        column.delete()
        messages.success(request, 'Column deleted.')
    return redirect('portal-dashboard')


# --- Process catalog --------------------------------------------------------

@login_required
def catalog_view(request):
    return render(request, 'portal/catalog.html', _catalog_context())


@login_required
@require_POST
def process_type_create(request):
    form = ProcessTypeForm(request.POST)
    if form.is_valid():
        process_type = form.save(commit=False)
        checklist_text = form.cleaned_data.get('checklist_text', '')
        process_type.checklist = [line.strip() for line in checklist_text.splitlines() if line.strip()]
        process_type.save()
        messages.success(request, 'Process type saved.')
        return redirect('portal-catalog')

    messages.error(request, 'Could not save the process type — check the highlighted fields.')
    context = _catalog_context(form=form)
    context['open_process_modal'] = True
    return render(request, 'portal/catalog.html', context)


@login_required
@admin_required
@require_POST
def process_type_delete(request, pk):
    process_type = get_object_or_404(ProcessType, pk=pk)
    if process_type.tasks.exists():
        messages.error(request, 'Cannot delete — tasks still use this process type.')
    else:
        process_type.delete()
        messages.success(request, 'Process type deleted.')
    return redirect('portal-catalog')


# --- Analytics --------------------------------------------------------------

@login_required
def analytics_view(request):
    tasks = list(Task.objects.select_related('process_type', 'assignee').all())
    completed = [t for t in tasks if t.completed_at]
    not_completed = [t for t in tasks if not t.completed_at]

    overdue_count = sum(1 for t in not_completed if compute_deadline_status(t) == 'overdue')
    at_risk_count = sum(1 for t in not_completed if compute_deadline_status(t) == 'at-risk')

    avg_turnaround_hours = None
    if completed:
        total_seconds = sum((t.completed_at - t.created_at).total_seconds() for t in completed)
        avg_turnaround_hours = round((total_seconds / len(completed)) / 3600, 1)

    def group_and_summarize(key_fn):
        """Bucket the tasks, then work out per-bucket stats."""
        buckets = {}
        for task in tasks:
            buckets.setdefault(key_fn(task), []).append(task)

        rows = []
        for name, items in buckets.items():
            done = [t for t in items if t.completed_at]
            overdue = sum(
                1 for t in items
                if not t.completed_at and compute_deadline_status(t) == 'overdue'
            )
            avg_hours = None
            if done:
                avg_hours = round(
                    sum((t.completed_at - t.created_at).total_seconds() for t in done)
                    / len(done) / 3600,
                    1,
                )
            rows.append({'name': name, 'count': len(items), 'overdue': overdue, 'avg_hours': avg_hours})

        rows.sort(key=lambda r: -r['count'])

        busiest = rows[0]['count'] if rows else 0
        for row in rows:
            on_time = row['count'] - row['overdue']
            row['pct'] = round(on_time / busiest * 100, 2) if busiest else 0
            row['overdue_pct'] = round(row['overdue'] / busiest * 100, 2) if busiest else 0

        return rows

    def staff_name(task):
        if not task.assignee:
            return 'Unassigned'
        return task.assignee.get_full_name() or task.assignee.get_username()

    context = {
        'active_tab': 'analytics',
        'total': len(tasks),
        'completed_count': len(completed),
        'awaiting_count': sum(1 for t in not_completed if t.awaiting_approval),
        'overdue_count': overdue_count,
        'at_risk_count': at_risk_count,
        'avg_turnaround_hours': avg_turnaround_hours,
        'by_process': group_and_summarize(lambda t: t.process_type.name),
        'by_staff': group_and_summarize(lambda t: t.assignee.get_full_name() if t.assignee else 'Unassigned'),
    }
    return render(request, 'portal/analytics.html', context)
# 'by_staff': group_and_summarize(staff_name),


@login_required
@require_POST
def comment_create(request, pk):
    task = get_object_or_404(Task, pk=pk)
    form = CommentForm(request.POST)

    if form.is_valid():
        comment = form.save(commit=False)
        comment.task = task
        comment.author = request.user
        comment.save()
    else:
        messages.error(request, 'Your comment was empty, so nothing was posted.')

    return redirect('task-edit', pk=pk)



@login_required
def task_edit(request, pk, edit_form=None):
    task = get_object_or_404(Task, pk=pk)
    context = _dashboard_context(request)
    # The template's edit modal reads `edit_form`; `task_form` stays bound to
    # the separate "New Task" modal on the same page.
    context['edit_form'] = edit_form if edit_form is not None else TaskForm(instance=task)
    context['edit_task'] = task
    context['comments'] = task.comments.select_related('author')
    context['comment_form'] = CommentForm()
    return render(request, 'portal/dashboard.html', context)


@login_required
@require_POST
def task_update(request, pk):
    task = get_object_or_404(Task, pk=pk)
    form = TaskForm(request.POST, instance=task)

    if not form.is_valid():
        # Re-open the edit modal with the errors instead of dropping the edit.
        messages.error(request, 'Could not update the task — check the highlighted fields.')
        return task_edit(request, pk, edit_form=form)

    updated = form.save(commit=False)
    apply_status_transition(updated)
    updated.save()

    if updated.awaiting_approval:
        messages.info(request, f'“{updated.title}” is now waiting for approval.')
    else:
        messages.success(request, 'Task updated.')
    return redirect('portal-dashboard')


@login_required
@require_POST
def task_approve(request, pk):
    task = get_object_or_404(Task, pk=pk)

    if not can_approve(request.user):
        messages.error(request, 'You do not have permission to approve tasks.')
        return redirect('portal-dashboard')

    if not task.awaiting_approval:
        messages.info(request, 'That task is not waiting for approval.')
        return redirect('portal-dashboard')

    task.approved_at = timezone.now()
    task.approved_by = request.user
    apply_status_transition(task)
    task.save()

    messages.success(request, f'Approved “{task.title}”.')
    return redirect('portal-dashboard')