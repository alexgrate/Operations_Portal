from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


# Create your models here.

class Profile(models.Model):
    ROLE_ADMIN = 'admin'
    ROLE_TEAM_LEAD = 'team_lead'
    ROLE_STAFF = 'staff'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_TEAM_LEAD, 'Team Lead'),
        (ROLE_STAFF, 'Operations Staff'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STAFF)
    branch = models.CharField(max_length=100, blank=True)  # for the Team Lead branch-filter story

    def __str__(self):
        return f'{self.user.get_full_name() or self.user.username} ({self.get_role_display()})'


# Whenever a new User is created (through my signup form),
# automatically give them a Profile too, defaulting to Operations
# Staff. Without this, existing code that assumes every user HAS a
# profile would crash for brand-new signups.

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)