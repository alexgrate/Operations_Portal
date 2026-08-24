from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save

CORPORATE_DOMAIN = '@dash-mfb.com'

ROLE_ADMIN = 'admin'
ROLE_DEPT_HEAD = 'dept_head'
ROLE_TEAM_LEAD = 'team_lead'
ROLE_STAFF = 'staff'

ROLE_CHOICES = [
    (ROLE_ADMIN, 'Admin'),
    (ROLE_DEPT_HEAD, 'Department Head'),
    (ROLE_TEAM_LEAD, 'Team Lead'),
    (ROLE_STAFF, 'Operations Staff'),
]

LEADERSHIP_ROLES = [ROLE_TEAM_LEAD, ROLE_DEPT_HEAD, ROLE_ADMIN]

ROLE_RANK = {ROLE_STAFF: 0, ROLE_TEAM_LEAD: 1, ROLE_DEPT_HEAD: 2, ROLE_ADMIN: 3}


def _role_of(user):
    profile = getattr(user, 'profile', None)
    return profile.role if profile is not None else ROLE_STAFF


def can_manage(actor, target):
    """Whether actor may edit, invite, or deactivate target.

    Strictly below the actor's own rank only - otherwise any team lead can
    deactivate the department head, or promote themselves through the edit
    form. Admins are the exception: they manage everyone (including other
    admins), because the top rank has no one above it to do so.
    """
    if _role_of(actor) == ROLE_ADMIN:
        return True
    return ROLE_RANK[_role_of(actor)] > ROLE_RANK[_role_of(target)]


def assignable_roles(actor):
    """Role choices the actor may hand out: strictly below their own rank.

    Admins can assign every role, including Admin - appointing a second
    admin must not require falling back to the Django superuser.
    """
    if _role_of(actor) == ROLE_ADMIN:
        return list(ROLE_CHOICES)
    limit = ROLE_RANK[_role_of(actor)]
    return [(role, label) for role, label in ROLE_CHOICES if ROLE_RANK[role] < limit]


class Team(models.Model):
    name = models.CharField(max_length=100, unique=True)
    lead = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='teams_led',
        limit_choices_to={
            'is_active': True,
            'profile__role__in': LEADERSHIP_ROLES,
        },
    )
    is_active = models.BooleanField(default=True)


    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class Profile(models.Model):
    ROLE_ADMIN = ROLE_ADMIN
    ROLE_DEPT_HEAD = ROLE_DEPT_HEAD
    ROLE_TEAM_LEAD = ROLE_TEAM_LEAD
    ROLE_STAFF = ROLE_STAFF
    ROLE_CHOICES = ROLE_CHOICES

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STAFF)
    teams = models.ManyToManyField(Team, blank=True, related_name='members')
    invite_sent_at = models.DateTimeField(null=True, blank=True)

    # Last time this person was emailed a list of work waiting on their
    # decision. Kept so the digest is rate limited per person, not per task.
    approval_digest_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.user.get_full_name() or self.user.username} ({self.get_role_display()})'


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if not created:
        return

    is_first_user = User.objects.count() == 1
    role = Profile.ROLE_ADMIN if (instance.is_superuser or is_first_user) else Profile.ROLE_STAFF

    Profile.objects.get_or_create(user=instance, defaults={'role': role})

@receiver(pre_save, sender=User)
def enforce_corporate_email(sender, instance, **kwargs):
    """Block non-corporate addresses on any path that creates or changes one.

    Only runs when the email is actually being set or changed. Django saves a
    User on every single login (update_last_login writes last_login), and
    validating there would lock out any account whose address predates this
    rule - including the superuser that set the system up.
    """
    update_fields = kwargs.get('update_fields')

    if update_fields is not None and 'email' not in update_fields:
        return

    email = (instance.email or '').strip().lower()

    if instance.pk:
        current = User.objects.filter(pk=instance.pk).values_list('email', flat=True).first()
        if current is not None and current.strip().lower() == email:
            return

    if not email:
        raise ValidationError('An email address is required.')

    if not email.endswith(CORPORATE_DOMAIN):
        raise ValidationError(
            f'Only {CORPORATE_DOMAIN} email addresses are allowed. Got: {email}'
        )

    clash = User.objects.filter(email__iexact=email)
    if instance.pk:
        clash = clash.exclude(pk=instance.pk)

    if clash.exists():
        raise ValidationError(f'An account already uses {email}.')

    instance.email = email
