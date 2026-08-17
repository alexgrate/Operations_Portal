"""Forms for tasks, process types and comments."""
from django import forms
from django.contrib.auth.models import User

from users.models import Team

from .models import Comment, ProcessType, Task


class PeopleChoiceField(forms.ModelChoiceField):
    """Show a person's name in dropdowns, not their username."""

    def label_from_instance(self, obj):
        return obj.get_full_name() or obj.get_username()


class TaskForm(forms.ModelForm):
    assignee = PeopleChoiceField(
        queryset=None, required=False, empty_label='Unassigned',
    )

    class Meta:
        model = Task
        # `team` decides which lead reviews the work, so it is required.
        fields = ['title', 'process_type', 'assignee', 'team', 'notes']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'e.g. KYC review - Damola J'}),
            'notes': forms.Textarea(attrs={
                'rows': 3, 'placeholder': 'Anything the assignee needs to know (optional)',
            }),
        }

    def __init__(self, *args, user=None, editable=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['team'].required = True
        self.fields['team'].empty_label = 'Choose a team…'
        self.fields['assignee'].queryset = User.objects.filter(is_active=True).order_by(
            'first_name', 'username',
        )

        # Locked fields are disabled rather than removed. Django ignores posted
        # data for a disabled field and keeps the initial value, so a crafted
        # POST cannot change what the page would not let you change.
        if editable is not None:
            for name, field in self.fields.items():
                if name not in editable:
                    field.disabled = True

        self.fields['team'].queryset = Team.objects.filter(is_active=True)

        # Staff put their own name on their own team's work. They may still
        # send work to another team, but they do not get to pick who in that
        # team does it - see clean(). Choosing someone else's workload for
        # them is the team lead's job.
        if user is not None and not _is_management(user):
            self.fields['assignee'].queryset = self.fields['assignee'].queryset.filter(pk=user.pk)
            self.fields['assignee'].initial = user

    def clean(self):
        cleaned = super().clean()
        assignee, team = cleaned.get('assignee'), cleaned.get('team')

        if not team:
            return cleaned

        # The reviewer is the team's lead, so the assignee has to be in it.
        if assignee:
            in_team = team.members.filter(user=assignee).exists()
            if not in_team and team.lead_id != assignee.id:
                self.add_error(
                    'assignee',
                    f'{assignee.get_full_name() or assignee.username} is not in {team.name}.',
                )

        # Work sent to a team the raiser is not in arrives unowned, so that
        # team's lead decides who picks it up.
        if (self.user is not None
                and not _is_management(self.user)
                and assignee
                and not team.members.filter(user=self.user).exists()):
            self.add_error(
                'assignee',
                f'You can send work to {team.name}, but their Team Lead assigns it. '
                'Leave the assignee as Unassigned.',
            )

        return cleaned


def _is_management(user):
    from .queues import is_management
    return is_management(user)


class ProcessTypeForm(forms.ModelForm):
    TARGET_UNIT_CHOICES = [('hours', 'Hours'), ('days', 'Days')]

    target_value = forms.FloatField(label='Turnaround target', min_value=0.5)
    target_unit = forms.ChoiceField(choices=TARGET_UNIT_CHOICES, initial='hours', label='Unit')
    checklist_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'One checklist item per line'}),
        label='Standard checklist (optional)',
    )

    class Meta:
        model = ProcessType
        fields = ['name', 'approval_level', 'requires_authorisation']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Account Opening - Retail'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Only target_hours is stored, so show it back in the largest unit
            # that divides cleanly. Without this a "1 day" target re-opens as
            # "24 hours" and retyping 1 silently makes it a 1 hour target.
            hours = self.instance.target_hours or 0
            if hours >= 24 and hours % 24 == 0:
                value, unit = hours / 24, 'days'
            else:
                value, unit = hours, 'hours'

            self.fields['target_value'].initial = int(value) if value == int(value) else value
            self.fields['target_unit'].initial = unit
            self.fields['checklist_text'].initial = '\n'.join(self.instance.checklist or [])


def _apply_target_and_checklist(form, process_type):
    value = form.cleaned_data['target_value']
    unit = form.cleaned_data['target_unit']
    process_type.target_hours = value * 24 if unit == 'days' else value

    text = form.cleaned_data.get('checklist_text', '')
    process_type.checklist = [line.strip() for line in text.splitlines() if line.strip()]


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['body']
        widgets = {'body': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a note…'})}
