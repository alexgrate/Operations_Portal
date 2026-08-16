"""Deadline reminder rules.

Deciding who to chase is kept apart from actually sending, so the rules can be
checked without a mail server. portal/management/commands/send_task_reminders.py
does the sending.
"""
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import Task

REMINDER, FINAL = 'reminder', 'final'


def candidates():
    """Every task worth looking at, cheapest filters first."""
    return (
        Task.objects
        .filter(
            archived_at__isnull=True,
            deadline__isnull=False,
            assignee__isnull=False,
            assignee__is_active=True,
        )
        .exclude(approval_stage=Task.STAGE_APPROVED)
        .exclude(assignee__email='')
        .select_related('process_type', 'assignee', 'team')
    )


def is_chaseable(task):
    """Whether it is fair to chase the assignee about this task at all.

    Repeats the checks candidates() already makes in SQL, so the rule holds
    for a task loaded any other way too. Never rely on the caller having
    filtered.

    Someone waiting on a manager cannot make the deadline move, so chasing
    them is noise. The same goes for work already handed in.
    """
    if not task.is_open:
        return False
    if not (task.deadline and task.assignee_id and task.assignee.email):
        return False
    if not task.assignee.is_active:
        return False
    if task.approval_stage in Task.AWAITING_AUTH:
        return False
    if task.approval_stage in Task.IN_REVIEW:
        return False
    return True


def minutes_left(task, now=None):
    now = now or timezone.now()
    return (task.deadline - now).total_seconds() / 60


def in_reminder_hours(now=None):
    start, end = settings.REMINDER_HOURS
    if start == end:
        return True
    hour = timezone.localtime(now or timezone.now()).hour
    if start < end:
        return start <= hour < end
    # An overnight window such as "22-6".
    return hour >= start or hour < end


def elapsed_percent(task, now=None):
    """How far along the way to the deadline this task is, 0 to 100+."""
    now = now or timezone.now()
    window = (task.deadline - task.created_at).total_seconds()
    if window <= 0:
        return 100.0
    return (now - task.created_at).total_seconds() / window * 100


def _milestone_due(task, now):
    """Milestone pacing: send at each fraction of this task's own deadline.

    reminders_sent doubles as the position in the list, so the next milestone
    is simply the one at that index. Self-limiting: when the list runs out, so
    do the reminders.
    """
    stops = settings.REMINDER_MILESTONES
    if task.reminders_sent >= len(stops):
        return False
    return elapsed_percent(task, now) >= stops[task.reminders_sent]


def _interval_due(task, now):
    """Fixed-clock pacing: the same gap for every task, whatever its target."""
    if task.reminders_sent >= settings.REMINDER_MAX_PER_TASK:
        return False
    since = task.reminder_sent_at or task.created_at
    return (now - since).total_seconds() / 60 >= settings.REMINDER_EVERY_MINUTES


def _overdue_due(task, now):
    """Past the deadline the pace drops right down. Chasing hourly does not
    make late work finish sooner; it just trains people to ignore the sender."""
    # Count from the deadline, not from a milestone sent before it, or the
    # first late chase would land at the wrong time.
    since = max(task.reminder_sent_at or task.deadline, task.deadline)
    return (now - since).total_seconds() / 60 >= settings.REMINDER_OVERDUE_EVERY_MINUTES


def due_kind(task, now=None):
    """Which email this task has earned right now, if any.

    The final warning wins when both are due, so nobody gets two emails in the
    same minute.
    """
    now = now or timezone.now()

    if not settings.REMINDERS_ENABLED or not is_chaseable(task):
        return None

    left = minutes_left(task, now)

    if not task.final_warning_at and left <= settings.REMINDER_FINAL_MINUTES:
        return FINAL

    # Quiet hours hold back routine chasing only. The final warning above
    # ignores them, which is the whole point of it.
    if not in_reminder_hours(now):
        return None

    if left <= 0:
        return REMINDER if _overdue_due(task, now) else None

    if settings.REMINDER_MODE == 'interval':
        return REMINDER if _interval_due(task, now) else None
    return REMINDER if _milestone_due(task, now) else None


def build_email(task, kind, now=None):
    """Subject and body for one reminder. Returns (subject, body)."""
    now = now or timezone.now()
    left = minutes_left(task, now)

    context = {
        'task': task,
        'is_final': kind == FINAL,
        'overdue': left <= 0,
        'time_left': _humanise(left),
        'overdue_by': _humanise(-left),
        'url': settings.SITE_URL + reverse('task-detail', kwargs={'pk': task.pk}),
    }
    return (
        render_to_string('portal/reminder_subject.txt', context).strip(),
        render_to_string('portal/reminder_email.html', context),
    )


def _humanise(minutes):
    """90.0 -> "1 hour 30 minutes". Used both ways round, so never negative."""
    minutes = max(0, int(round(minutes)))
    hours, mins = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days:
        parts.append(f'{days} day' + ('s' if days > 1 else ''))
    if hours:
        parts.append(f'{hours} hour' + ('s' if hours > 1 else ''))
    if mins and not days:
        parts.append(f'{mins} minute' + ('s' if mins > 1 else ''))

    return ' '.join(parts) or 'less than a minute'


def record_sent(task, kind, now=None):
    """Mark the reminder as delivered. Only called after the send succeeds, so
    a mail failure is retried on the next run instead of being lost."""
    now = now or timezone.now()

    if kind == FINAL:
        task.final_warning_at = now
        task.save(update_fields=['final_warning_at'])
        return

    task.reminder_sent_at = now
    task.reminders_sent = _next_count(task, now)
    task.save(update_fields=['reminder_sent_at', 'reminders_sent'])


def _next_count(task, now):
    """Where the counter lands after a routine reminder.

    In milestone mode it jumps to however many milestones have actually been
    passed. If cron were down for a day, a task now at 95% would otherwise
    fire the 50% and 80% emails back to back on the next two runs.
    """
    if settings.REMINDER_MODE == 'interval' or minutes_left(task, now) <= 0:
        return task.reminders_sent + 1

    passed = sum(1 for stop in settings.REMINDER_MILESTONES
                 if elapsed_percent(task, now) >= stop)
    return max(task.reminders_sent + 1, passed)
