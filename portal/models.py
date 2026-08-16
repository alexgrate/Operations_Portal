from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class ProcessType(models.Model):
    """A kind of operational work, with its turnaround target and checklist."""
    name = models.CharField(max_length=200)
    target_hours = models.FloatField()
    checklist = models.JSONField(default=list, blank=True)

    APPROVAL_NONE = 'none'
    APPROVAL_LEAD = 'lead'
    APPROVAL_LEAD_HEAD = 'lead_head'
    APPROVAL_CHOICES = [
        (APPROVAL_NONE, 'No approval needed'),
        (APPROVAL_LEAD, 'Team Lead only'),
        (APPROVAL_LEAD_HEAD, 'Team Lead, then Department Head'),
    ]

    approval_level = models.CharField(
        max_length=20, choices=APPROVAL_CHOICES, default=APPROVAL_NONE,
    )

    requires_authorisation = models.BooleanField(
        default=False,
        help_text='Tick for work that must be permitted before it starts, not just checked after.',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Task(models.Model):
    """A single piece of operational work.

    There is one state axis: `approval_stage`, plus `started_at` to tell
    "not started" from "in progress". No board columns - where a task sits is
    derived from how far through the work and sign-off it is.
    """
    title = models.CharField(max_length=300)
    notes = models.TextField(blank=True)
    process_type = models.ForeignKey(ProcessType, on_delete=models.PROTECT, related_name='tasks')

    assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_tasks', limit_choices_to={'is_active': True},
    )
    team = models.ForeignKey(
        'users.Team', on_delete=models.PROTECT, null=True, blank=True,
        related_name='tasks', limit_choices_to={'is_active': True},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    checklist_snapshot = models.JSONField(default=list, blank=True)
    checklist_done = models.JSONField(default=dict, blank=True)

    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='archived_tasks',
    )

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='raised_tasks',
    )
    authorised_at = models.DateTimeField(null=True, blank=True)
    authorised_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='authorised_tasks',
    )

    # Written by the send_task_reminders command. Kept on the row rather than
    # worked out on the fly so a restarted or repeated run cannot send the
    # same reminder twice.
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    reminders_sent = models.PositiveIntegerField(default=0)
    final_warning_at = models.DateTimeField(null=True, blank=True)

    STAGE_AUTH_LEAD = 'auth_lead'
    STAGE_AUTH_HEAD = 'auth_head'
    STAGE_DRAFT = 'draft'
    STAGE_LEAD_REVIEW = 'lead_review'
    STAGE_HEAD_REVIEW = 'head_review'
    STAGE_APPROVED = 'approved'
    STAGE_RETURNED = 'returned'
    STAGE_CHOICES = [
        (STAGE_AUTH_LEAD, 'Awaiting Team Lead permission'),
        (STAGE_AUTH_HEAD, 'Awaiting Department Head permission'),
        (STAGE_DRAFT, 'Not submitted'),
        (STAGE_LEAD_REVIEW, 'With Team Lead'),
        (STAGE_HEAD_REVIEW, 'With Department Head'),
        (STAGE_APPROVED, 'Approved'),
        (STAGE_RETURNED, 'Returned'),
    ]
    IN_REVIEW = (STAGE_LEAD_REVIEW, STAGE_HEAD_REVIEW)
    AWAITING_AUTH = (STAGE_AUTH_LEAD, STAGE_AUTH_HEAD)

    approval_stage = models.CharField(
        max_length=20, choices=STAGE_CHOICES, default=STAGE_DRAFT,
    )

    # When the task arrived at the stage it is in now. Maintained in save().
    # The approval digests use it to tell a manager what is new since the last
    # one, rather than listing the same queue over and over.
    stage_since = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stage_on_load = self.approval_stage

    def save(self, *args, **kwargs):
        now = timezone.now()

        if self._state.adding:
            if not self.checklist_snapshot:
                self.checklist_snapshot = list(self.process_type.checklist or [])
            if not self.deadline:
                self.deadline = now + timedelta(hours=self.process_type.target_hours)
            self.stage_since = now
        elif self.approval_stage != self._stage_on_load:
            self.stage_since = now
            # A partial save would otherwise drop the change on the floor.
            if kwargs.get('update_fields'):
                kwargs['update_fields'] = [*kwargs['update_fields'], 'stage_since']

        super().save(*args, **kwargs)
        self._stage_on_load = self.approval_stage


    @property
    def is_archived(self):
        return self.archived_at is not None

    @property
    def is_open(self):
        return self.approval_stage != self.STAGE_APPROVED and not self.is_archived

    @property
    def needs_authorisation(self):
        """Permission is required and has not been given yet."""
        return bool(self.process_type.requires_authorisation and not self.authorised_at)

    @property
    def state_label(self):
        """One phrase describing where this task is."""
        if self.is_archived:
            return 'Archived'
        if self.approval_stage == self.STAGE_AUTH_LEAD:
            return 'Awaiting permission'
        if self.approval_stage == self.STAGE_AUTH_HEAD:
            return 'Awaiting permission'
        if self.approval_stage == self.STAGE_APPROVED:
            return 'Completed'
        if self.approval_stage == self.STAGE_LEAD_REVIEW:
            return 'With Team Lead'
        if self.approval_stage == self.STAGE_HEAD_REVIEW:
            return 'With Dept Head'
        if self.approval_stage == self.STAGE_RETURNED:
            return 'Returned'
        return 'In progress' if self.started_at else 'Not started'

    @property
    def state_key(self):
        """Slug for the state, used as a CSS class."""
        return self.state_label.lower().replace(' ', '-')

    @property
    def urgency(self):
        """on-track / at-risk / overdue - only meaningful while still open."""
        if not self.is_open or not self.deadline:
            return None

        now = timezone.now()
        if now >= self.deadline:
            return 'overdue'

        window = (self.deadline - self.created_at).total_seconds()
        if window <= 0:
            return 'overdue'

        if (now - self.created_at).total_seconds() / window >= 0.7:
            return 'at-risk'
        return 'on-track'

    @property
    def finished_late(self):
        return bool(
            self.completed_at and self.deadline and self.completed_at > self.deadline
        )

    @property
    def checklist(self):
        """This task's own checklist.

        Falls back to the process type for tasks raised before snapshots
        existed, so nothing loses its checklist retrospectively.
        """
        return self.checklist_snapshot or self.process_type.checklist or []

    @property
    def checklist_items(self):
        """This task's checklist, paired with whether each item is ticked."""
        done = self.checklist_done or {}
        return [
            {'index': i, 'text': text, 'done': bool(done.get(str(i)))}
            for i, text in enumerate(self.checklist)
        ]

    @property
    def checklist_differs_from_process(self):
        """True when the process type has been edited since this task started."""
        return bool(
            self.checklist_snapshot
            and self.checklist_snapshot != (self.process_type.checklist or [])
        )

    @property
    def checklist_outstanding(self):
        return sum(1 for item in self.checklist_items if not item['done'])

    @property
    def checklist_done_count(self):
        return sum(1 for item in self.checklist_items if item['done'])

    @property
    def checklist_total(self):
        return len(self.checklist)

    @property
    def due_label(self):
        """A short deadline for table rows - "2h", "3d", "45m", "Overdue"."""
        if not self.is_open:
            return None
        if not self.deadline:
            return None

        remaining = (self.deadline - timezone.now()).total_seconds()
        if remaining <= 0:
            return 'Overdue'

        days, hours = divmod(int(remaining // 3600), 24)
        if days:
            return f'{days}d'
        if hours:
            return f'{hours}h'
        return f'{max(1, int(remaining // 60))}m'


class Approval(models.Model):
    """One sign-off decision. The full chain is kept, never overwritten."""
    STAGE_AUTH_LEAD = 'auth_lead'
    STAGE_AUTH_HEAD = 'auth_head'
    STAGE_LEAD = 'lead'
    STAGE_HEAD = 'head'
    DECISION_APPROVED = 'approved'
    DECISION_RETURNED = 'returned'

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='approvals')
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='approval_actions',
    )
    stage = models.CharField(max_length=10)
    decision = models.CharField(max_length=10)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.task.title}: {self.stage} {self.decision}'

    @property
    def stage_label(self):
        return {
            self.STAGE_AUTH_LEAD: 'Team Lead, permission',
            self.STAGE_AUTH_HEAD: 'Department Head, permission',
            self.STAGE_LEAD: 'Team Lead',
            self.STAGE_HEAD: 'Department Head',
        }.get(self.stage, self.stage)

    @property
    def is_authorisation(self):
        return self.stage in (self.STAGE_AUTH_LEAD, self.STAGE_AUTH_HEAD)


def attachment_path(instance, filename):
    """Store under a random name so a file cannot be found by guessing a URL.
    The name the person uploaded is kept on the row instead."""
    return f'task_attachments/{instance.task_id}/{uuid4().hex}{Path(filename).suffix.lower()}'


class Attachment(models.Model):
    """A document or image on a task. Served only through a view that checks
    who is asking - never from a public media URL."""

    IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic'}

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=attachment_path)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return self.original_name

    @property
    def suffix(self):
        return Path(self.original_name).suffix.lower()

    @property
    def is_image(self):
        return self.suffix in self.IMAGE_SUFFIXES

    @property
    def size_label(self):
        kb = self.size / 1024
        if kb < 1024:
            return f'{max(1, round(kb))} KB'
        return f'{kb / 1024:.1f} MB'


class Comment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.author} on {self.task}'