"""Sign-off rules: draft -> team lead -> department head -> approved."""
from django.utils import timezone

from users.models import Profile

from .models import Approval, ProcessType, Task


# --- Who is who -------------------------------------------------------------

def _role(user):
    profile = getattr(user, 'profile', None)
    return profile.role if profile else None


def is_dept_head(user):
    """Department Heads sign off the second stage. Admins can stand in."""
    return bool(
        user.is_authenticated
        and (user.is_superuser or _role(user) in (Profile.ROLE_DEPT_HEAD, Profile.ROLE_ADMIN))
    )


def is_lead_of(user, task):
    """True only for the lead of the team this particular task belongs to."""
    return bool(
        user.is_authenticated
        and task.team_id
        and task.team.lead_id == user.id
    )


# --- What this task needs ---------------------------------------------------

def required_stages(task):
    """The sign-offs this task needs, in order."""
    level = task.process_type.approval_level

    if level == ProcessType.APPROVAL_LEAD:
        return [Approval.STAGE_LEAD]
    if level == ProcessType.APPROVAL_LEAD_HEAD:
        return [Approval.STAGE_LEAD, Approval.STAGE_HEAD]
    return []


# --- Permission to start ----------------------------------------------------

def opening_stage(task, creator):
    """Where a task starts, based on who raised it.

    Only applies to process types marked as needing permission. The more
    senior the raiser, the fewer permissions outstanding.
    """
    if not task.process_type.requires_authorisation:
        return Task.STAGE_DRAFT

    if is_dept_head(creator):
        return Task.STAGE_DRAFT
    if _role(creator) == Profile.ROLE_TEAM_LEAD:
        return Task.STAGE_AUTH_HEAD
    return Task.STAGE_AUTH_LEAD


def apply_opening_state(task, creator):
    """Set the starting stage, and record a self-authorisation when the raiser
    is senior enough to bypass the gate. Without the record the task would
    still count as needing permission and nobody could start it."""
    task.approval_stage = opening_stage(task, creator)

    if (task.process_type.requires_authorisation
            and task.approval_stage == Task.STAGE_DRAFT
            and not task.authorised_at):
        task.authorised_at = timezone.now()
        task.authorised_by = creator

    return task.approval_stage


def can_authorise(user, task):
    """May this person give permission at the stage the task is sitting in?"""
    if task.approval_stage == Task.STAGE_AUTH_LEAD:
        return is_lead_of(user, task) or is_dept_head(user)
    if task.approval_stage == Task.STAGE_AUTH_HEAD:
        return is_dept_head(user)
    return False


def authorise(task, actor, comment=''):
    """Give permission. Staff-raised work needs the lead, then the head."""
    stage = (Approval.STAGE_AUTH_LEAD
             if task.approval_stage == Task.STAGE_AUTH_LEAD
             else Approval.STAGE_AUTH_HEAD)

    Approval.objects.create(
        task=task, actor=actor, stage=stage,
        decision=Approval.DECISION_APPROVED, comment=comment,
    )

    if stage == Approval.STAGE_AUTH_LEAD:
        task.approval_stage = Task.STAGE_AUTH_HEAD
    else:
        task.approval_stage = Task.STAGE_DRAFT
        task.authorised_at = timezone.now()
        task.authorised_by = actor

    task.save()
    return task.approval_stage


def decline(task, actor, comment):
    """Refuse permission. The task stops; the reason is recorded."""
    stage = (Approval.STAGE_AUTH_LEAD
             if task.approval_stage == Task.STAGE_AUTH_LEAD
             else Approval.STAGE_AUTH_HEAD)

    Approval.objects.create(
        task=task, actor=actor, stage=stage,
        decision=Approval.DECISION_RETURNED, comment=comment,
    )

    task.approval_stage = Task.STAGE_RETURNED
    task.save()
    return task.approval_stage


def can_request_authorisation(user, task):
    """A declined task can be amended and put up for permission again."""
    return bool(
        task.needs_authorisation
        and task.approval_stage == Task.STAGE_RETURNED
        and (task.assignee_id == user.id or task.created_by_id == user.id)
    )


def request_authorisation(task, requester):
    apply_opening_state(task, requester)
    task.save(update_fields=['approval_stage', 'authorised_at', 'authorised_by'])
    return task.approval_stage


def can_start(user, task):
    """Work cannot begin until any required permission has been given."""
    return bool(
        task.assignee_id == user.id
        and not task.started_at
        and task.is_open
        and not task.needs_authorisation
        and task.approval_stage in (Task.STAGE_DRAFT, Task.STAGE_RETURNED)
    )


# --- Sign-off after the work -------------------------------------------------

def can_submit(user, task):
    """Only the assignee submits, and only once any permission is given."""
    return bool(
        task.assignee_id == user.id
        and not task.needs_authorisation
        and task.approval_stage in (Task.STAGE_DRAFT, Task.STAGE_RETURNED)
    )


def can_review(user, task):
    """May this person approve or send back the task at its current stage?"""
    if task.approval_stage == Task.STAGE_LEAD_REVIEW:
        return is_lead_of(user, task) or is_dept_head(user)
    if task.approval_stage == Task.STAGE_HEAD_REVIEW:
        return is_dept_head(user)
    return False


def stage_on_submit(task):
    """Where a task lands the moment it is submitted."""
    stages = required_stages(task)

    if not stages:
        return Task.STAGE_APPROVED

    lead_id = task.team.lead_id if task.team_id else None

    if stages[0] == Approval.STAGE_LEAD and task.assignee_id == lead_id:
        remaining = stages[1:]
        return Task.STAGE_HEAD_REVIEW if remaining else Task.STAGE_APPROVED

    return Task.STAGE_LEAD_REVIEW


def _current_stage_name(task):
    return (
        Approval.STAGE_LEAD
        if task.approval_stage == Task.STAGE_LEAD_REVIEW
        else Approval.STAGE_HEAD
    )


def _mark_approved(task):
    task.approval_stage = Task.STAGE_APPROVED
    if not task.completed_at:
        task.completed_at = timezone.now()


def submit(task):
    """The assignee says the work is done."""
    task.submitted_at = timezone.now()
    task.approval_stage = stage_on_submit(task)

    if task.approval_stage == Task.STAGE_APPROVED:
        _mark_approved(task)
    else:
        task.completed_at = None

    task.save()
    return task.approval_stage


def approve(task, actor, comment=''):
    """A reviewer signs the task off at whatever stage it is sitting in."""
    stage = _current_stage_name(task)

    Approval.objects.create(
        task=task, actor=actor, stage=stage,
        decision=Approval.DECISION_APPROVED, comment=comment,
    )

    if stage == Approval.STAGE_LEAD and Approval.STAGE_HEAD in required_stages(task):
        task.approval_stage = Task.STAGE_HEAD_REVIEW
    else:
        _mark_approved(task)

    task.save()
    return task.approval_stage


def send_back(task, actor, comment):
    """A reviewer returns the task to the assignee. The reason is required."""
    stage = _current_stage_name(task)

    Approval.objects.create(
        task=task, actor=actor, stage=stage,
        decision=Approval.DECISION_RETURNED, comment=comment,
    )

    task.approval_stage = Task.STAGE_RETURNED
    task.submitted_at = None
    task.completed_at = None

    # The work is starting again, so the reminder allowance starts again too.
    task.reminders_sent = 0
    task.reminder_sent_at = None

    task.save()
    return task.approval_stage
