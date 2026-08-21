from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordResetForm

from .models import Profile, Team



class EmployeeAddForm(forms.ModelForm):
    """Form used by Admin to create an Operations Portal employee."""

    role = forms.ChoiceField(
        choices=Profile.ROLE_CHOICES,
        initial=Profile.ROLE_STAFF,
        label="Portal role",
    )

    teams = forms.ModelMultipleChoiceField(
        queryset=Team.objects.filter(is_active=True),
        required=False,
        label="Team(s)",
        help_text="Which team lead reviews this person's work. A staff member may sit in more than one.",
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "teams",
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if not email.endswith("@dash-mfb.com"):
            raise forms.ValidationError(
                "Only Dash MFB corporate email addresses are allowed."
            )

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "A user with this email already exists."
            )

        return email

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()

        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(
                "A user with this username already exists."
            )

        return username


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    extra = 0


class CustomUserAdmin(UserAdmin):
    add_form = EmployeeAddForm

    add_fieldsets = (
        (
            "Employee account",
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "first_name",
                    "last_name",
                    "role",
                    "teams",
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        """Only Superusers and application Admins can create users."""
        if request.user.is_superuser:
            return True

        return (
            request.user.is_authenticated
            and hasattr(request.user, "profile")
            and request.user.profile.role == Profile.ROLE_ADMIN
        )

    def has_delete_permission(self, request, obj=None):
        """Only Superusers and application Admins can delete users."""
        if request.user.is_superuser:
            return True

        return (
            request.user.is_authenticated
            and hasattr(request.user, "profile")
            and request.user.profile.role == Profile.ROLE_ADMIN
        )

    def get_inline_instances(self, request, obj=None):
        """
        Don't show Profile inline while creating a user because
        the Profile is automatically created by the signal.
        """
        if obj is None:
            return []

        return [ProfileInline(self.model, self.admin_site)]

    def save_model(self, request, obj, form, change):
        if not change:
            import secrets

            temporary_password = secrets.token_urlsafe(32)
            obj.set_password(temporary_password)

            super().save_model(request, obj, form, change)

            profile = obj.profile
            profile.role = form.cleaned_data["role"]
            profile.save()
            profile.teams.set(form.cleaned_data["teams"])

            reset_form = PasswordResetForm({"email": obj.email})

            if reset_form.is_valid():
                reset_form.save(
                    request=request,
                    use_https=request.is_secure(),
                    from_email=None,
                    email_template_name="users/password_reset_email.txt",
                    html_email_template_name="users/password_reset_email.html",
                    subject_template_name="users/password_reset_subject.txt",
                )

        else:
            super().save_model(request, obj, form, change)


    def delete_model(self, request, obj):
        if obj == request.user:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You cannot delete your own account.")

        super().delete_model(request, obj)



@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "team_names"]
    list_filter = ["role", "teams"]
    filter_horizontal = ["teams"]
    search_fields = ["user__username", "user__email"]

    def team_names(self, obj):
        return ', '.join(t.name for t in obj.teams.all()) or '-'
    team_names.short_description = 'Teams'


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "lead", "member_count", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name"]

    @admin.display(description="Members")
    def member_count(self, obj):
        return obj.members.count()


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)

admin.site.site_header = "Dash MFB - Operations Portal"
admin.site.site_title = "Operations Portal admin"
admin.site.index_title = "Manage teams, staff and processes"
