"""Forms for tasks, process types and comments."""
from django import forms
from django.contrib.auth.models import User

from users.models import ROLE_ADMIN, ROLE_AUDITOR, Team

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

        assignable = (
            User.objects.filter(is_active=True)
            .exclude(profile__role__in=[ROLE_ADMIN, ROLE_AUDITOR])
            .exclude(is_superuser=True)
        )

        current = getattr(self.instance, 'assignee_id', None)
        if current:
            assignable = assignable | User.objects.filter(pk=current)

        self.fields['assignee'].queryset = assignable.distinct().order_by(
            'first_name', 'username',
        )


        if editable is not None:
            for name, field in self.fields.items():
                if name not in editable:
                    field.disabled = True

        self.fields['team'].queryset = Team.objects.filter(is_active=True)

        if user is not None and not _is_management(user):
            self.fields['assignee'].queryset = self.fields['assignee'].queryset.filter(pk=user.pk)
            self.fields['assignee'].initial = user

    def clean(self):
        cleaned = super().clean()
        assignee, team = cleaned.get('assignee'), cleaned.get('team')

        if not team:
            return cleaned

        if assignee:
            in_team = team.members.filter(user=assignee).exists()
            if not in_team and team.lead_id != assignee.id:
                self.add_error(
                    'assignee',
                    f'{assignee.get_full_name() or assignee.username} is not in {team.name}.',
                )

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
        fields = ['name', 'approval_level']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Account Opening - Retail'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
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
