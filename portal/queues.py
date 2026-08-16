"""The work queues shown in the sidebar, and who may see each one."""
from django.db.models import Q

from users.models import LEADERSHIP_ROLES, ROLE_ADMIN, ROLE_DEPT_HEAD, ROLE_TEAM_LEAD

from .models import Task


def role_of(user):
    profile = getattr(user, 'profile', None)
    return profile.role if profile else None


def is_head(user):
    return user.is_superuser or role_of(user) in (ROLE_DEPT_HEAD, ROLE_ADMIN)


def is_lead(user):
    return role_of(user) == ROLE_TEAM_LEAD


def is_management(user):
    return user.is_superuser or role_of(user) in LEADERSHIP_ROLES


def _base():
    """Every queue except Archived hides archived work."""
    return (
        Task.objects
        .select_related('process_type', 'assignee', 'team', 'team__lead')
        .filter(archived_at__isnull=True)
    )


def _archived_base():
    return (
        Task.objects
        .select_related('process_type', 'assignee', 'team', 'team__lead', 'archived_by')
        .filter(archived_at__isnull=False)
    )


def archived(user):
    """Tidied away, kept for the record. Management only."""
    if is_management(user):
        return _archived_base()
    return _archived_base().none()


# --- the queues -------------------------------------------------------------

def my_work(user):
    """Mine, still to do - includes anything sent back to me."""
    return _base().filter(
        assignee=user,
        approval_stage__in=[Task.STAGE_DRAFT, Task.STAGE_RETURNED, *Task.AWAITING_AUTH],
    )


def to_authorise(user):
    """Waiting for this person to permit work to start."""
    if is_head(user):
        return _base().filter(approval_stage__in=Task.AWAITING_AUTH)
    if is_lead(user):
        return _base().filter(approval_stage=Task.STAGE_AUTH_LEAD, team__lead=user)
    return _base().none()


def awaiting_me(user):
    """Sitting at a stage this person is the one to sign off."""
    if is_head(user):
        # The head signs the second stage, and can stand in on the first.
        return _base().filter(approval_stage__in=Task.IN_REVIEW)
    if is_lead(user):
        return _base().filter(
            approval_stage=Task.STAGE_LEAD_REVIEW, team__lead=user,
        )
    return _base().none()


def submitted_by_me(user):
    """Passed on, and now waiting on somebody else.

    Two ways that happens: handed in for review, or raised for another team to
    pick up. Without the second, someone who sends work elsewhere has no way
    of ever finding it again.
    """
    handed_over = (
        Q(created_by=user)
        & ~Q(approval_stage=Task.STAGE_APPROVED)
        & (Q(assignee__isnull=True) | ~Q(assignee=user))
    )
    return _base().filter(
        Q(assignee=user, approval_stage__in=Task.IN_REVIEW) | handed_over
    ).distinct()


def my_team(user):
    """Everything in the teams this person leads."""
    if is_head(user):
        return _base().filter(approval_stage__in=[
            Task.STAGE_DRAFT, Task.STAGE_RETURNED, *Task.IN_REVIEW,
        ])
    if is_lead(user):
        return _base().filter(team__lead=user).exclude(approval_stage=Task.STAGE_APPROVED)
    return _base().none()


def completed(user):
    qs = _base().filter(approval_stage=Task.STAGE_APPROVED)
    if is_head(user):
        return qs
    if is_lead(user):
        return qs.filter(Q(team__lead=user) | Q(assignee=user))
    return qs.filter(assignee=user)


QUEUES = {
    'my-work':   {'label': 'My work',   'fn': my_work,         'everyone': True,
                  'icon': 'ri-inbox-line'},
    'authorise': {'label': 'Permission', 'fn': to_authorise,   'everyone': False,
                  'icon': 'ri-lock-unlock-line'},
    'awaiting':  {'label': 'Sign-off',  'fn': awaiting_me,     'everyone': False,
                  'icon': 'ri-shield-check-line'},
    'submitted': {'label': 'Submitted', 'fn': submitted_by_me, 'everyone': True,
                  'icon': 'ri-send-plane-line'},
    'team':      {'label': 'My team',   'fn': my_team,         'everyone': False,
                  'icon': 'ri-team-line'},
    'completed': {'label': 'Completed', 'fn': completed,       'everyone': True,
                  'icon': 'ri-checkbox-circle-line'},
    'archived':  {'label': 'Archived',  'fn': archived,        'everyone': False,
                  'icon': 'ri-archive-line'},
}


def visible_queues(user):
    """The queues this person should see in the sidebar, in order."""
    return [
        (key, spec['label'], spec['icon'])
        for key, spec in QUEUES.items()
        if spec['everyone'] or is_management(user)
    ]


def queue_counts(user):
    return {key: spec['fn'](user).count() for key, spec in QUEUES.items()}


def get_queue(key, user):
    spec = QUEUES.get(key)
    if not spec:
        return None, None
    if not (spec['everyone'] or is_management(user)):
        return None, None
    return spec['label'], spec['fn'](user)


def can_see_task(user, task):
    """Anyone involved, plus management.

    Whoever raised it counts as involved. They may have sent it to a team they
    are not in, and they still need to follow what happened to it.
    """
    return bool(
        is_management(user)
        or task.assignee_id == user.id
        or task.created_by_id == user.id
        or (task.team_id and task.team.lead_id == user.id)
    )
