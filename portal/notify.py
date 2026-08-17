"""Emails sent the moment something happens, rather than on the cron round.

Two moments matter enough not to wait for a digest: work arriving for sign-off,
and the decision coming back. Both are somebody standing still until they hear.

Nothing in here raises. A mail server being down must never undo a sign-off
that already happened, so every failure is logged and swallowed. The digests in
portal/digests.py are the safety net: anything missed here still turns up there.
"""
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from users.models import Profile

from .models import Approval, Task

logger = logging.getLogger(__name__)


def task_url(task):
    return settings.SITE_URL + reverse('task-detail', kwargs={'pk': task.pk})


def _send(recipients, subject_template, body_template, context):
    """Send to everyone who has an address. Never raises."""
    addresses = sorted({r.email for r in recipients if r and r.email})
    if not addresses:
        return 0

    try:
        send_mail(
            subject=render_to_string(subject_template, context).strip(),
            message=render_to_string(body_template, context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=addresses,
            fail_silently=False,
        )
    except Exception:
        logger.exception('Notification to %s failed for task %s',
                         addresses, context.get('task') and context['task'].pk)
        return 0

    return len(addresses)


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
                 'portal/review_request_email.html', {
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
                 'portal/decision_email.html', {
                     'task': task,
                     'actor': actor,
                     'returned': decision == Approval.DECISION_RETURNED,
                     'finished': task.approval_stage == Task.STAGE_APPROVED,
                     'now_with_head': task.approval_stage == Task.STAGE_HEAD_REVIEW,
                     'comment': comment,
                     'url': task_url(task),
                 })
