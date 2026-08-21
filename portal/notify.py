"""Emails sent the moment something happens, rather than on the cron round.

Three moments matter enough not to wait for a digest: work landing on somebody,
work arriving for sign-off, and the decision coming back. Each one leaves a
person standing still until they hear.

Nothing in here raises. A mail server being down must never undo a sign-off
that already happened, so every failure is logged and swallowed. The digests in
portal/digests.py are the safety net: anything missed here still turns up there.
"""
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from users.models import Profile

from .models import Approval, Task

logger = logging.getLogger(__name__)


def task_url(task):
    return settings.SITE_URL + reverse('task-detail', kwargs={'pk': task.pk})


def _send(recipients, subject_template, body_stem, context):
    """Send to everyone who has an address. Never raises.

    Sent as both plain text and HTML from `<stem>.txt` and `<stem>.html`. The
    text part is not a courtesy: some clients refuse HTML, some people read
    mail in a terminal, and a message with no text alternative scores worse
    with spam filters.
    """
    addresses = sorted({r.email for r in recipients if r and r.email})
    if not addresses:
        return 0

    try:
        message = EmailMultiAlternatives(
            subject=render_to_string(subject_template, context).strip(),
            body=render_to_string(f'{body_stem}.txt', context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=addresses,
        )
        message.attach_alternative(render_to_string(f'{body_stem}.html', context), 'text/html')
        message.send(fail_silently=False)
    except Exception:
        logger.exception('Notification to %s failed for task %s',
                         addresses, context.get('task') and context['task'].pk)
        return 0

    return len(addresses)


def assigned(task, actor):
    """Tell somebody that work has landed on them.

    Called from the two paths that can set an assignee: raising a task with a
    name already on it, and editing one to change who is doing it. Assigning
    from the Django admin deliberately sends nothing - a post_save signal would
    catch that path but would also fire on every other save, and a management
    command touching tasks in bulk must never email the whole department.
    """
    if not settings.ASSIGNMENT_EMAILS_ENABLED or not task.assignee:
        return 0

    # You know what you just gave yourself.
    if task.assignee_id == getattr(actor, 'id', None):
        return 0

    return _send([task.assignee], 'portal/assignment_subject.txt',
                 'portal/assignment_email', {
                     'task': task,
                     'actor': actor,
                     'not_started': not task.started_at,
                     'url': task_url(task),
                 })


def handed_to_team(task, actor):
    """Tell a Team Lead that unowned work has arrived in their queue.

    Somebody sending work to a team they are not in leaves it unassigned on
    purpose: that team's lead decides who picks it up, not the sender. Without
    this the handover is silent and the lead finds out from the digest hours
    later, while the deadline has been running the whole time.
    """
    if not settings.ASSIGNMENT_EMAILS_ENABLED:
        return 0
    if task.assignee_id or not task.team_id:
        return 0

    lead = task.team.lead
    if not lead or lead.id == getattr(actor, 'id', None):
        return 0

    return _send([lead], 'portal/handover_subject.txt',
                 'portal/handover_email', {
                     'task': task,
                     'actor': actor,
                     'url': task_url(task),
                 })


def reviewers_at_stage(task):
    """Who can act on this task where it now sits."""
    if task.approval_stage == Task.STAGE_LEAD_REVIEW:
        lead = task.team.lead if task.team_id else None
        return [lead] if lead else _heads()
    if task.approval_stage == Task.STAGE_HEAD_REVIEW:
        return _heads()
    return []


def _heads():
    return list(
        User.objects.filter(is_active=True, profile__role=Profile.ROLE_DEPT_HEAD)
        .exclude(email='')
    ) or list(
        User.objects.filter(is_active=True, profile__role=Profile.ROLE_ADMIN)
        .exclude(email='')
    )


def submitted_for_review(task, submitter):
    """Tell the reviewer that work is waiting on them."""
    reviewers = reviewers_at_stage(task)
    if not reviewers:
        logger.warning('Task %s submitted with nobody to review it', task.pk)
        return 0

    return _send(reviewers, 'portal/review_request_subject.txt',
                 'portal/review_request_email', {
                     'task': task,
                     'submitter': submitter,
                     'is_head_stage': task.approval_stage == Task.STAGE_HEAD_REVIEW,
                     'url': task_url(task),
                 })


def decision_made(task, actor, decision, comment=''):
    """Tell the assignee what the reviewer decided.

    Three outcomes: signed off and finished, passed up a level, or sent back.
    """
    if not task.assignee:
        return 0

    # No point emailing somebody about their own decision.
    if task.assignee_id == getattr(actor, 'id', None):
        return 0

    return _send([task.assignee], 'portal/decision_subject.txt',
                 'portal/decision_email', {
                     'task': task,
                     'actor': actor,
                     'returned': decision == Approval.DECISION_RETURNED,
                     'finished': task.approval_stage == Task.STAGE_APPROVED,
                     'now_with_head': task.approval_stage == Task.STAGE_HEAD_REVIEW,
                     'comment': comment,
                     'url': task_url(task),
                 })
