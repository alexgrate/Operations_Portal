"""Telling managers that work is sitting waiting for their decision.

The counterpart to portal/reminders.py, which chases the person doing the work.
Deliberately one email per manager listing everything, not one per task: a
Department Head covering several teams would otherwise be buried.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import queues
from .models import Task


def approvers():
    """Everyone who can hold work up, so everyone worth emailing.

    Ordinary staff never hold a decision, so they are dropped here rather than
    running two empty queue queries each.
    """
    people = (
        User.objects
        .filter(is_active=True)
        .exclude(email='')
        .select_related('profile')
        .order_by('id')
    )
    return [user for user in people if queues.is_management(user)]


def waiting_on(user):
    """Everything sitting with this person, awaiting their sign-off.

    Reuses the sidebar queues so the email can never disagree with what the
    person sees when they follow the link.
    """
    pending = list(queues.awaiting_me(user))

    # A Department Head sees several teams, so a task can arrive twice.
    unique = {task.pk: task for task in pending}

    # Tightest deadline first. Anything without one goes last, and never gets
    # compared against a real date.
    far_future = timezone.now() + timedelta(days=3650)
    return sorted(unique.values(), key=lambda t: t.deadline or far_future)


def is_due(user, pending, now=None):
    """Whether to email this person now.

    Sends when something new turns up, no more often than the minimum gap, and
    otherwise repeats slowly while anything is still outstanding. Quiet hours
    apply - none of this is urgent enough to wake somebody.
    """
    now = now or timezone.now()

    if not settings.APPROVAL_DIGESTS_ENABLED or not pending:
        return False

    from .reminders import in_reminder_hours
    if not in_reminder_hours(now):
        return False

    last = getattr(getattr(user, 'profile', None), 'approval_digest_at', None)
    if last is None:
        return True

    minutes_since = (now - last).total_seconds() / 60
    if minutes_since < settings.APPROVAL_DIGEST_MIN_GAP_MINUTES:
        return False

    if any(task.stage_since and task.stage_since > last for task in pending):
        return True

    return minutes_since >= settings.APPROVAL_DIGEST_EVERY_MINUTES


def build_email(user, pending, now=None):
    """Subject and body for one manager's digest. Returns (subject, body)."""
    now = now or timezone.now()
    last = getattr(getattr(user, 'profile', None), 'approval_digest_at', None)

    rows = [
        {
            'task': task,
            'is_new': bool(last is None or (task.stage_since and task.stage_since > last)),
            'waiting': _waited(task, now),
            'url': settings.SITE_URL + reverse('task-detail', kwargs={'pk': task.pk}),
        }
        for task in pending
    ]

    context = {
        'user': user,
        'rows': rows,
        'total': len(rows),
        'overdue': sum(1 for r in rows if r['task'].urgency == 'overdue'),
        'signoff_url': settings.SITE_URL + reverse('queue', kwargs={'key': 'awaiting'}),
    }
    return (
        render_to_string('portal/digest_subject.txt', context).strip(),
        render_to_string('portal/digest_email.txt', context),
        render_to_string('portal/digest_email.html', context),
    )


def _waited(task, now):
    """How long this task has been sitting at this stage, roughly."""
    since = task.stage_since or task.created_at
    hours = int((now - since).total_seconds() // 3600)
    if hours < 1:
        return 'under an hour'
    if hours < 24:
        return f'{hours} hour' + ('s' if hours > 1 else '')
    days = hours // 24
    return f'{days} day' + ('s' if days > 1 else '')


def record_sent(user, now=None):
    profile = user.profile
    profile.approval_digest_at = now or timezone.now()
    profile.save(update_fields=['approval_digest_at'])
