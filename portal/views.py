from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from users.models import Profile, can_manage

from . import approvals, notify, queues
from .pagination import paginate
from .forms import CommentForm, ProcessTypeForm, TaskForm, _apply_target_and_checklist
from .models import Approval, Attachment, ProcessType, Task


def login_view(request):
    if request.user.is_authenticated:
        return redirect('portal-home')

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
                return redirect(_safe_next(request) or reverse('portal-home'))

    return render(request, 'users/login.html', {'email': email})


def _safe_next(request):
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


def management_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not queues.is_management(request.user):
            messages.error(request, 'That area is for Team Leads and above.')
            return redirect('portal-home')
        return view_func(request, *args, **kwargs)
    return wrapper


def head_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not queues.is_head(request.user):
            messages.error(request, 'That area is for the Department Head.')
            return redirect('portal-home')
        return view_func(request, *args, **kwargs)
    return wrapper


@login_required
def home(request):
    """Land on the queue that most likely needs this person."""
    if queues.awaiting_me(request.user).exists():
        return redirect('queue', key='awaiting')
    return redirect('queue', key='my-work')


@login_required
def queue_view(request, key):
    label, qs = queues.get_queue(key, request.user)
    if qs is None:
        raise PermissionDenied

    search = request.GET.get('q', '').strip()
    if search:
        qs = qs.filter(title__icontains=search)

    process = request.GET.get('process', '')
    if process.isdigit():
        qs = qs.filter(process_type_id=process)

    tasks = list(qs)
    tasks.sort(key=lambda t: (
        0 if t.urgency == 'overdue' else 1,
        t.deadline or timezone.now() + timedelta(days=3650),
    ))

    page = paginate(request, tasks)

    return render(request, 'portal/queue.html', {
        'queue_key': key,
        'queue_label': label,
        'tasks': page['items'],
        'search': search,
        **page,
        'selected_process': process,
        'process_types': ProcessType.objects.all(),
        'task_form': TaskForm(user=request.user),
        'can_create': True,
    })


@login_required
def task_detail(request, pk, form=None):
    task = get_object_or_404(
        Task.objects.select_related('process_type', 'assignee', 'team', 'team__lead'), pk=pk,
    )
    if not queues.can_see_task(request.user, task):
        raise PermissionDenied

    return render(request, 'portal/task_detail.html', {
        'task': task,
        'form': form if form is not None else TaskForm(
            instance=task, user=request.user,
            editable=approvals.editable_fields(request.user, task),
        ),
        'comments': task.comments.select_related('author'),
        'attachments': task.attachments.select_related('uploaded_by'),
        'max_upload_mb': settings.MAX_UPLOAD_BYTES // (1024 * 1024),
        'comment_form': CommentForm(),
        'sign_offs': task.approvals.select_related('actor'),
        'can_submit': approvals.can_submit(request.user, task),
        'can_review': approvals.can_review(request.user, task),
        'can_edit': approvals.can_edit(request.user, task),
        'editable': approvals.editable_fields(request.user, task),
        'can_start': approvals.can_start(request.user, task),
    })


@login_required
@require_POST
def task_create(request):
    form = TaskForm(request.POST, user=request.user)
    if form.is_valid():
        task = form.save(commit=False)
        task.created_by = request.user
        approvals.apply_opening_state(task, request.user)
        task.save()

        # Exactly one of these fires: assigned() needs somebody on it,
        # handed_to_team() needs nobody on it.
        notify.assigned(task, request.user)
        notify.handed_to_team(task, request.user)

        messages.success(request, 'Task created.')
        return redirect('task-detail', pk=task.pk)

    messages.error(request, 'Could not create the task - check the highlighted fields.')
    return render(request, 'portal/queue.html', {
        'queue_key': 'my-work',
        'queue_label': 'My work',
        'tasks': list(queues.my_work(request.user)),
        'process_types': ProcessType.objects.all(),
        'task_form': form,
        'open_new_task': True,
        'can_create': True,
    })


@login_required
@require_POST
def task_update(request, pk):
    task = get_object_or_404(Task, pk=pk)

    editable = approvals.editable_fields(request.user, task)
    if not editable:
        if task.approval_stage in Task.IN_REVIEW:
            messages.error(
                request,
                'This task is with a reviewer. Ask them to send it back if it needs changing.',
            )
            return redirect('task-detail', pk=pk)
        raise PermissionDenied

    was_process = task.process_type_id
    was_assignee = task.assignee_id
    was_team = task.team_id
    form = TaskForm(request.POST, instance=task, user=request.user, editable=editable)
    if not form.is_valid():
        messages.error(request, 'Could not save - check the highlighted fields.')
        return task_detail(request, pk, form=form)

    task = form.save(commit=False)

    # A different process type means a different target and a different
    # checklist. Leaving the old ones behind produces a task whose deadline and
    # checklist disagree with its own rules.
    if task.process_type_id != was_process:
        task.checklist_snapshot = list(task.process_type.checklist or [])
        task.checklist_done = {}
        task.deadline = task.created_at + timedelta(hours=task.process_type.target_hours)
        messages.info(request, 'Process type changed, so the deadline and checklist were reset.')

    task.save()

    # Only when the work moved onto somebody new. An unrelated edit is not
    # news for anybody.
    if task.assignee_id and task.assignee_id != was_assignee:
        notify.assigned(task, request.user)
    elif not task.assignee_id and (task.team_id != was_team or was_assignee):
        # Newly ownerless, or moved to a team that has not picked it up yet.
        notify.handed_to_team(task, request.user)

    messages.success(request, 'Task updated.')
    return redirect('task-detail', pk=pk)


@login_required
@require_POST
def task_start(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not approvals.can_start(request.user, task):
        raise PermissionDenied

    task.started_at = timezone.now()
    task.save(update_fields=['started_at'])
    return redirect('task-detail', pk=pk)


@login_required
@require_POST
def task_checklist(request, pk):
    """Tick or untick one checklist item."""
    task = get_object_or_404(Task, pk=pk)
    if task.assignee_id != request.user.id:
        raise PermissionDenied

    index = request.POST.get('index', '')
    if index.isdigit():
        done = dict(task.checklist_done or {})
        done[index] = not done.get(index, False)
        task.checklist_done = done
        if not task.started_at:
            task.started_at = timezone.now()
        task.save(update_fields=['checklist_done', 'started_at'])

    return redirect('task-detail', pk=pk)


@login_required
@require_POST
def task_archive(request, pk):
    """Hide a task from the working queues, keeping it and its sign-offs.

    Tasks are never deleted - the approval chain on a completed task is the
    record an auditor asks for.
    """
    task = get_object_or_404(Task, pk=pk)
    if not queues.is_head(request.user):
        raise PermissionDenied

    if task.is_archived:
        task.archived_at = None
        task.archived_by = None
        task.save(update_fields=['archived_at', 'archived_by'])
        messages.success(request, f'"{task.title}" restored.')
        return redirect('task-detail', pk=pk)

    task.archived_at = timezone.now()
    task.archived_by = request.user
    task.save(update_fields=['archived_at', 'archived_by'])
    messages.success(request, f'"{task.title}" archived. Find it under Archived.')
    return redirect('queue', key='archived')


@login_required
@require_POST
def task_submit(request, pk):
    task = get_object_or_404(Task, pk=pk)

    if not approvals.can_submit(request.user, task):
        messages.error(request, 'Only the person assigned to a task can submit it.')
        return redirect('task-detail', pk=pk)

    if task.checklist_outstanding:
        messages.error(
            request,
            f'Finish the checklist first - {task.checklist_outstanding} item(s) still to tick.',
        )
        return redirect('task-detail', pk=pk)

    if approvals.submit(task) == Task.STAGE_APPROVED:
        messages.success(request, f'"{task.title}" is complete.')
    else:
        notify.submitted_for_review(task, request.user)
        messages.success(
            request,
            f'"{task.title}" sent for review. It is not complete until it is signed off.',
        )
    return redirect('portal-home')


@login_required
@require_POST
def task_approve(request, pk):
    task = get_object_or_404(Task, pk=pk)

    if not approvals.can_review(request.user, task):
        messages.error(request, 'You cannot sign this task off at its current stage.')
        return redirect('task-detail', pk=pk)

    comment = request.POST.get('comment', '').strip()
    stage = approvals.approve(task, request.user, comment)

    notify.decision_made(task, request.user, Approval.DECISION_APPROVED, comment)
    if stage == Task.STAGE_HEAD_REVIEW:
        # Handed up a level, so the next reviewer needs telling too.
        notify.submitted_for_review(task, task.assignee or request.user)
        messages.success(request, f'Approved - "{task.title}" now goes to the Department Head.')
    else:
        messages.success(request, f'"{task.title}" is fully approved.')
    return redirect('queue', key='awaiting')


@login_required
@require_POST
def task_return(request, pk):
    task = get_object_or_404(Task, pk=pk)

    if not approvals.can_review(request.user, task):
        messages.error(request, 'You cannot return this task at its current stage.')
        return redirect('task-detail', pk=pk)

    comment = request.POST.get('comment', '').strip()
    if not comment:
        messages.error(request, 'Say why you are returning it - the assignee needs to know.')
        return redirect('task-detail', pk=pk)

    approvals.send_back(task, request.user, comment)
    notify.decision_made(task, request.user, Approval.DECISION_RETURNED, comment)
    messages.success(request, f'Returned to {task.assignee or "the assignee"}.')
    return redirect('queue', key='awaiting')


@login_required
@require_POST
def attachment_upload(request, pk):
    """Attach one or more files to a task."""
    task = get_object_or_404(Task, pk=pk)
    if not queues.can_see_task(request.user, task):
        raise PermissionDenied

    files = request.FILES.getlist('files')
    if not files:
        messages.error(request, 'Choose at least one file.')
        return redirect('task-detail', pk=pk)

    saved, rejected = 0, []
    for upload in files:
        suffix = Path(upload.name).suffix.lower()

        if suffix not in settings.ALLOWED_UPLOAD_SUFFIXES:
            rejected.append(f'{upload.name} (type not allowed)')
            continue
        if upload.size > settings.MAX_UPLOAD_BYTES:
            limit = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
            rejected.append(f'{upload.name} (over {limit} MB)')
            continue

        Attachment.objects.create(
            task=task, file=upload, original_name=upload.name[:255],
            size=upload.size, uploaded_by=request.user,
        )
        saved += 1

    if saved:
        messages.success(request, f'{saved} file{"s" if saved != 1 else ""} attached.')
    if rejected:
        messages.error(request, 'Not attached: ' + '; '.join(rejected))
    return redirect('task-detail', pk=pk)


@login_required
def attachment_download(request, pk):
    """The only way to read an attachment. Checks the task first."""
    attachment = get_object_or_404(Attachment.objects.select_related('task'), pk=pk)
    if not queues.can_see_task(request.user, attachment.task):
        raise PermissionDenied

    # inline lets images preview; everything else the browser will offer to save
    return FileResponse(
        attachment.file.open('rb'),
        as_attachment=not attachment.is_image,
        filename=attachment.original_name,
    )


@login_required
@require_POST
def attachment_delete(request, pk):
    attachment = get_object_or_404(Attachment.objects.select_related('task'), pk=pk)
    task = attachment.task

    # Whoever attached it, or management, can remove it.
    if not (attachment.uploaded_by_id == request.user.id or queues.is_management(request.user)):
        raise PermissionDenied

    attachment.file.delete(save=False)
    attachment.delete()
    messages.success(request, 'Attachment removed.')
    return redirect('task-detail', pk=task.pk)


@login_required
@require_POST
def comment_create(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not queues.can_see_task(request.user, task):
        raise PermissionDenied

    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.task = task
        comment.author = request.user
        comment.save()
    else:
        messages.error(request, 'Your comment was empty, so nothing was posted.')
    return redirect('task-detail', pk=pk)


@login_required
def catalog_view(request, form=None):
    page = paginate(request, ProcessType.objects.all())

    return render(request, 'portal/catalog.html', {
        'process_types': page['items'],
        **page,
        'form': form if form is not None else ProcessTypeForm(),
        'can_manage': queues.is_management(request.user),
    })


@login_required
@management_required
@require_POST
def process_type_create(request):
    form = ProcessTypeForm(request.POST)
    if form.is_valid():
        process_type = form.save(commit=False)
        _apply_target_and_checklist(form, process_type)
        process_type.save()
        messages.success(request, 'Process type saved.')
        return redirect('portal-catalog')

    messages.error(request, 'Could not save - check the highlighted fields.')
    return catalog_view(request, form=form)


@login_required
@management_required
@require_POST
def process_type_update(request, pk):
    process_type = get_object_or_404(ProcessType, pk=pk)
    form = ProcessTypeForm(request.POST, instance=process_type)
    if form.is_valid():
        updated = form.save(commit=False)
        _apply_target_and_checklist(form, updated)
        updated.save()
        messages.success(request, 'Process type updated.')
        return redirect('portal-catalog')

    messages.error(request, 'Could not save - check the highlighted fields.')
    return process_type_edit(request, pk, form=form)

@login_required
@management_required
def process_type_edit(request, pk, form=None):
    process_type = get_object_or_404(ProcessType, pk=pk)
    return render(request, 'portal/process_type_form.html', {
        'process_type': process_type,
        'form': form if form is not None else ProcessTypeForm(instance=process_type),
        'active_tab': 'catalog',
        # Tasks already in flight keep their own frozen checklist and deadline,
        # so the edit only affects work raised from now on. Say so.
        'open_tasks': process_type.tasks.exclude(approval_stage=Task.STAGE_APPROVED).count(),
    })


@login_required
@head_required
@require_POST
def process_type_delete(request, pk):
    process_type = get_object_or_404(ProcessType, pk=pk)
    if process_type.tasks.exists():
        messages.error(request, 'Cannot delete - tasks still use this process type.')
    else:
        process_type.delete()
        messages.success(request, 'Process type deleted.')
    return redirect('portal-catalog')


@login_required
@management_required
def analytics_view(request):
    tasks = list(Task.objects.select_related('process_type', 'assignee', 'team'))
    done = [t for t in tasks if t.completed_at]
    open_tasks = [t for t in tasks if t.is_open]

    def mean(values):
        clean = [v for v in values if v is not None]
        return round(sum(clean) / len(clean), 1) if clean else None

    # Two clocks, kept apart. One number would hide whether the delay was the
    # work or the sign-off, which is the whole question.
    avg_staff_hours = mean([t.staff_hours for t in done])
    avg_review_hours = mean([t.review_hours for t in done])
    avg_hours = mean([
        round((t.completed_at - t.work_started_from).total_seconds() / 3600, 1) for t in done
    ])

    def summarise(key_fn):
        buckets = {}
        for task in tasks:
            buckets.setdefault(key_fn(task), []).append(task)

        rows = []
        for name, items in buckets.items():
            finished = [t for t in items if t.completed_at]
            submitted = [t for t in items if t.submitted_at]
            rows.append({
                'name': name,
                'count': len(items),
                'overdue': sum(1 for t in items if t.urgency == 'overdue'),
                'avg_hours': mean([
                    round((t.completed_at - t.work_started_from).total_seconds() / 3600, 1)
                    for t in finished
                ]),
                'staff_hours': mean([t.staff_hours for t in items]),
                'review_hours': mean([t.review_hours for t in items]),
                'late_submissions': sum(1 for t in submitted if t.staff_late),
                'submitted_count': len(submitted),
            })

        rows.sort(key=lambda r: -r['count'])
        top = rows[0]['count'] if rows else 0
        for row in rows:
            row['pct'] = round((row['count'] - row['overdue']) / top * 100, 2) if top else 0
            row['overdue_pct'] = round(row['overdue'] / top * 100, 2) if top else 0
        return rows

    return render(request, 'portal/analytics.html', {
        'total': len(tasks),
        'completed_count': len(done),
        'open_count': len(open_tasks),
        'overdue_count': sum(1 for t in open_tasks if t.urgency == 'overdue'),
        'awaiting_count': sum(1 for t in tasks if t.approval_stage in Task.IN_REVIEW),
        'avg_hours': avg_hours,
        'avg_staff_hours': avg_staff_hours,
        'avg_review_hours': avg_review_hours,
        'in_review_count': sum(1 for t in tasks if t.waiting_on_review),
        'by_process': summarise(lambda t: t.process_type.name),
        'by_staff': summarise(
            lambda t: (t.assignee.get_full_name() or t.assignee.get_username())
            if t.assignee else 'Unassigned'
        ),
    })


@login_required
@management_required
def staff_view(request):
    """Who is in which team, and what they are carrying."""
    # Deactivated people stay listed so they can be reactivated.
    people = (
        User.objects.select_related('profile')
        .prefetch_related('profile__teams', 'assigned_tasks')
        .order_by('-is_active', 'first_name', 'username')
    )

    rows = []
    for person in people:
        open_tasks = [t for t in person.assigned_tasks.all() if t.is_open]
        rows.append({
            'user': person,
            'role': person.profile.get_role_display() if hasattr(person, 'profile') else '-',
            'teams': list(person.profile.teams.all()) if hasattr(person, 'profile') else [],
            'open': len(open_tasks),
            'overdue': sum(1 for t in open_tasks if t.urgency == 'overdue'),
            'manageable': can_manage(request.user, person),
        })

    page = paginate(request, rows)
    return render(request, 'portal/staff.html',
                  {'rows': page['items'], 'active_tab': 'staff', **page})
