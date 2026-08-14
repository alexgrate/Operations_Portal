from django.db import models
from django.contrib.auth.models import User 

# Create your models here.


class ProcessType(models.Model):
    """Equivalent to one object in your JS `processTypes` array."""
    name = models.CharField(max_length=200)
    # Always stored in hours, same convention as your JS (days get
    # converted to hours before saving, on the frontend).
    target_hours = models.FloatField()
    # JSONField lets us store a plain list of strings, like your
    # `checklist: ['Verify ID document', ...]` array, without needing
    # a separate table for it.
    checklist = models.JSONField(default=list, blank=True)

    # When on, a task of this type is not finished the moment it reaches a
    # "Completes" column - it waits for an Admin or Team Lead to sign it off.
    requires_approval = models.BooleanField(
        default=False,
        help_text='Tasks of this type need a sign-off before they count as completed.',
    )

    def __str__(self):
        return self.name


class Column(models.Model):
    """Equivalent to one object in your JS `columns` array."""
    name = models.CharField(max_length=100)
    starts = models.BooleanField(default=False)
    completes = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)  # controls left-to-right board order

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


class Task(models.Model):
    """Equivalent to one object in your JS `tasks` array."""
    title = models.CharField(max_length=300)

    # ForeignKey = "this task points at one row in another table."
    # on_delete=PROTECT means: you can't delete a ProcessType or Column
    # while tasks still reference it — Django will block it and tell you,
    # rather than silently orphaning data.
    process_type = models.ForeignKey(ProcessType, on_delete=models.PROTECT, related_name='tasks')
    status = models.ForeignKey(Column, on_delete=models.PROTECT, related_name='tasks')

    # SET_NULL here instead: if a staff account is deleted, we keep the
    # task but blank out who it was assigned to, rather than blocking
    # the deletion entirely.
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tasks')

    # auto_now_add=True: Django stamps this automatically the moment the
    # row is created — you never set it yourself, same idea as
    # `createdAt: Date.now()` in your JS.
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)

    escalation_sent = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)

    # Mirrors your `checklistDone: { "0": true, "1": false }` object.
    checklist_done = models.JSONField(default=dict, blank=True)

    # Approval sign-off. A task whose process type requires approval parks in
    # `awaiting_approval` when it reaches a completing column, and only gets a
    # `completed_at` once someone signs it off.
    awaiting_approval = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_tasks'
    )

    def __str__(self):
        return self.title



class Comment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.author} on {self.task}'



class Escalation(models.Model):
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='escalations',
    )

    team_lead = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='escalations',
    )

    message = models.CharField(max_length=500)

    created_at = models.DateTimeField(auto_now_add=True)

    read_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.task.title} → {self.team_lead}'