from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from .models import ProcessType, Column, Task
from django.utils import timezone
from .forms import TaskForm, ColumnForm, ProcessTypeForm
from django.shortcuts import get_object_or_404



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

    return render(request, 'users/login.html', {'email': email})


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


@login_required
def dashboard(request):
    return render(request, 'portal/dashboard.html')



def catalog(request):
    return render(request, "portal/catalog.html")



def analytics(request):
    return render(request, "portal/analytics.html")



def compute_deadline_status(task):
    target_hours = task.process_type.target_hours
    deadline = task.created_at + timezone.timedelta(hours=target_hours)
    if task.completed_at:
        return 'done'
    now = timezone.now()
    elapsed_seconds = (now - task.created_at).total_seconds()
    target_seconds = target_hours * 3600
    pct = elapsed_seconds / target_seconds if target_seconds else 1
    if pct >= 1:
        return 'overdue'
    if pct >= 0.7:
        return 'at-risk'
    return 'on-track'


# @login_required
def dashboard(request):
    from django.contrib.auth.models import User

    columns = Column.objects.all()
    tasks = Task.objects.select_related('process_type', 'status', 'assignee').all()

    process_filter = request.GET.get('process')
    assignee_filter = request.GET.get('assignee')
    if process_filter:
        tasks = tasks.filter(process_type_id=process_filter)
    if assignee_filter:
        tasks = tasks.filter(assignee_id=assignee_filter)

    columns_with_tasks = []
    for col in columns:
        col_tasks = [t for t in tasks if t.status_id == col.id]
        for t in col_tasks:
            t.deadline_status = compute_deadline_status(t)
        columns_with_tasks.append({'column': col, 'tasks': col_tasks})

    context = {
        'columns_with_tasks': columns_with_tasks,
        'all_columns': columns,
        'process_types': ProcessType.objects.all(),
        'staff': User.objects.all(),
        'task_form': TaskForm(),
        'column_form': ColumnForm(),
        'selected_process': process_filter or '',
        'selected_assignee': assignee_filter or '',
    }
    return render(request, 'portal/dashboard.html', context)



# class ProcessTypeViewSet(viewsets.ModelViewSet):
#     queryset = ProcessType.objects.all()
#     serializer_class = ProcessTypeSerializer
#     permission_classes = [permissions.IsAuthenticated]


# class ColumnViewSet(viewsets.ModelViewSet):
#     queryset = Column.objects.all()
#     serializer_class = ColumnSerializer
#     permission_classes = [permissions.IsAuthenticated]


# class TaskViewSet(viewsets.ModelViewSet):
#     queryset = Task.objects.all()
#     serializer_class = TaskSerializer
#     permission_classes = [permissions.IsAuthenticated]


def task_create(request):
    if request.method == 'POST':
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            if task.status.starts:
                task.started_at = timezone.now()
            if task.status.completes:
                task.completed_at = timezone.now()
            task.save()
            messages.success(request, 'Task created.')
        else:
            messages.error(request, 'Could not create task — check the form.')
    return redirect('portal-dashboard')


def column_create(request):
    if request.method == 'POST':
        form = ColumnForm(request.POST)
        if form.is_valid():
            column = form.save(commit=False)
            column.order = Column.objects.count() + 1
            column.save()
            messages.success(request, 'Column added.')
    return redirect('portal-dashboard')


def task_move(request, pk):
    """The server-rendered version of moveTask() — triggered by the
    'Move to' <select> on each card auto-submitting its tiny form."""
    task = get_object_or_404(Task, pk=pk)
    if request.method == 'POST':
        new_status = get_object_or_404(Column, pk=request.POST.get('status'))
        task.status = new_status
        if new_status.starts and not task.started_at:
            task.started_at = timezone.now()
        if new_status.completes and not task.completed_at:
            task.completed_at = timezone.now()
        if not new_status.completes:
            task.completed_at = None
        task.save()
    return redirect('portal-dashboard')


def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if request.method == 'POST':
        task.delete()
        messages.success(request, 'Task deleted.')
    return redirect('portal-dashboard')


# @login_required(login_url='login')
def column_delete(request, pk):
    column = get_object_or_404(Column, pk=pk)
    if request.method == 'POST':
        if Task.objects.filter(status=column).exists():
            messages.error(request, 'Move or delete tasks in this column first.')
        else:
            column.delete()
    return redirect('portal-dashboard')


# --- Process Catalog ---------------------------------------------------
def catalog_view(request):
    context = {
        'process_types': ProcessType.objects.all(),
        'form': ProcessTypeForm(),
    }
    return render(request, 'portal/catalog.html', context)


def process_type_create(request):
    if request.method == 'POST':
        form = ProcessTypeForm(request.POST)
        if form.is_valid():
            process_type = form.save(commit=False)
            # Turn the textarea (one line per item) into the JSON
            # list the model actually stores.
            checklist_text = form.cleaned_data.get('checklist_text', '')
            process_type.checklist = [line.strip() for line in checklist_text.splitlines() if line.strip()]
            process_type.save()
            messages.success(request, 'Process type saved.')
        else:
            messages.error(request, 'Could not save - check the form.')
    return redirect('portal-catalog')

# @login_required(login_url='login')
def process_type_delete(request, pk):
    process_type = get_object_or_404(ProcessType, pk=pk)
    if request.method == 'POST':
        if process_type.tasks.exists():
            messages.error(request, 'Cannot delete — tasks still use this process type.')
        else:
            process_type.delete()
    return redirect('portal-catalog')



# --- Analytics --------------------------------------------------------------
# @login_required(login_url='login')
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
        """The Python twin of groupBy() + the analytics breakdown
        rendering from ops-portal.js — same two-step idea: bucket
        first, then compute stats per bucket."""
        buckets = {}
        for t in tasks:
            buckets.setdefault(key_fn(t), []).append(t)

        rows = []
        for name, items in buckets.items():
            done = [t for t in items if t.completed_at]
            overdue = sum(1 for t in items if not t.completed_at and compute_deadline_status(t) == 'overdue')
            avg_hours = None
            if done:
                avg_hours = round(sum((t.completed_at - t.created_at).total_seconds() for t in done) / len(done) / 3600, 1)
            rows.append({'name': name, 'count': len(items), 'overdue': overdue, 'avg_hours': avg_hours})
        return sorted(rows, key=lambda r: -r['count'])

    context = {
        'total': len(tasks),
        'completed_count': len(completed),
        'overdue_count': overdue_count,
        'at_risk_count': at_risk_count,
        'avg_turnaround_hours': avg_turnaround_hours,
        'by_process': group_and_summarize(lambda t: t.process_type.name),
        'by_staff': group_and_summarize(lambda t: t.assignee.get_full_name() if t.assignee else 'Unassigned'),
    }
    return render(request, 'portal/analytics.html', context)
