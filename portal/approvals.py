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
    """The sign-offs this task needs, in order. Never empty."""
    level = task.process_type.approval_level

    if level == ProcessType.APPROVAL_HEAD:
        return [Approval.STAGE_HEAD]
    if level == ProcessType.APPROVAL_LEAD_HEAD:
        return [Approval.STAGE_LEAD, Approval.STAGE_HEAD]
    return [Approval.STAGE_LEAD]


# --- Permission to start ----------------------------------------------------

def opening_stage(task, creator):
    """Where a task starts.

    The permission-before-work gate was withdrawn after the September demo:
    the owners decided no task needs releasing before it can be started. Every
    task now opens ready to work on.

    This returns a constant rather than being deleted because the stages it
    used to return still exist on historical rows, and because reinstating the
    gate is a one-function change if that decision is revisited.
    """
    return Task.STAGE_DRAFT


def apply_opening_state(task, creator):
    """Set the starting stage for a newly raised task."""
    task.approval_stage = opening_stage(task, creator)
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


def editable_fields(user, task):
    """Which parts of a task this person may still change.

    The single source of truth for both the form and the view. Hiding the Edit
    box in a template is not enforcement: a posted form reaches the view either
    way.

    Changing a task under the reviewer looking at it is the thing this mostly
    exists to stop. If it needs changing at that point, the reviewer sends it
    back and the assignee gets it properly.
    """
    if not task.is_open:
        return set()

    management = _is_management(user)

    if task.approval_stage in Task.IN_REVIEW:
        # Handed in. A manager may still re-route it; nobody rewrites it.
        return {'assignee', 'team'} if management else set()

    fields = set()
    if management or task.assignee_id == user.id:
        fields |= {'title', 'notes'}

    if management:
        fields |= {'assignee', 'team'}
        # The process type sets the deadline, the checklist and who signs off.
        # Once work has started all three are in flight, so it stops moving.
        if not task.started_at:
            fields.add('process_type')

    return fields


def can_edit(user, task):
    return bool(editable_fields(user, task))


def _is_management(user):
    from .queues import is_management
    return is_management(user)


def can_review(user, task):
    """May this person approve or send back the task at its current stage?"""
    if task.approval_stage == Task.STAGE_LEAD_REVIEW:
        return is_lead_of(user, task) or is_dept_head(user)
    if task.approval_stage == Task.STAGE_HEAD_REVIEW:
        return is_dept_head(user)
    return False


def effective_stages(task):
    """The sign-offs this task actually needs.

    Any stage the assignee would be signing off for themselves is dropped, so
    a Team Lead's own work goes up to the Department Head rather than closing
    on their own say-so. The Head has nobody above them, which is why their
    own work is the one case that ends up with nothing left to do.
    """
    lead_id = task.team.lead_id if task.team_id else None
    return [
        stage for stage in required_stages(task)
        if not (stage == Approval.STAGE_LEAD and task.assignee_id == lead_id)
        and not (stage == Approval.STAGE_HEAD and _is_head(task.assignee))
    ]


def _is_head(user):
    return bool(user and is_dept_head(user))


def stage_on_submit(task):
    """Where a task lands the moment it is submitted."""
    remaining = effective_stages(task)

    if Approval.STAGE_LEAD in remaining:
        return Task.STAGE_LEAD_REVIEW
    if Approval.STAGE_HEAD in remaining:
        return Task.STAGE_HEAD_REVIEW

    # Every stage this task needed was one the assignee would have been
    # signing for themselves. A Team Lead still answers to the Department
    # Head, so their work goes up rather than closing. The Head answers to
    # nobody inside this system, which is the only case that closes on submit.
    if _is_head(task.assignee):
        return Task.STAGE_APPROVED
    return Task.STAGE_HEAD_REVIEW


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

    if stage == Approval.STAGE_LEAD and Approval.STAGE_HEAD in effective_stages(task):
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
