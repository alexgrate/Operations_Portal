from django import forms
from django.contrib.auth.models import User

from .models import CORPORATE_DOMAIN, Profile, Team, assignable_roles


class StaffForm(forms.Form):
    """Onboard one person. The account is created without a password; they
    set their own from the invite link."""

    full_name = forms.CharField(
        max_length=150, label='Full name',
        widget=forms.TextInput(attrs={'placeholder': 'Tunde Bakare', 'autocomplete': 'name'}),
    )
    email = forms.EmailField(
        label='Corporate email',
        widget=forms.EmailInput(attrs={'placeholder': f'name.surname{CORPORATE_DOMAIN}'}),
    )
    role = forms.ChoiceField(choices=Profile.ROLE_CHOICES, initial=Profile.ROLE_STAFF)
    teams = forms.ModelMultipleChoiceField(
        queryset=Team.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Whose lead reviews their work. Someone may sit in more than one.',
    )

    def __init__(self, *args, instance=None, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if actor is not None:
            # Only roles below the actor's own; the ChoiceField then rejects
            # anything else on POST, not just in the rendered dropdown.
            self.fields['role'].choices = assignable_roles(actor)
        if instance is not None:
            self.fields['full_name'].initial = instance.get_full_name()
            self.fields['email'].initial = instance.email
            self.fields['role'].initial = instance.profile.role
            self.fields['teams'].initial = instance.profile.teams.all()

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()

        if not email.endswith(CORPORATE_DOMAIN):
            raise forms.ValidationError(f'Must be a {CORPORATE_DOMAIN} address.')
        if len(email) > 150:
            raise forms.ValidationError('That email address is too long.')

        clash = User.objects.filter(email__iexact=email)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('Someone already has that address.')

        return email

    def clean_full_name(self):
        name = self.cleaned_data['full_name'].strip()
        if not name:
            raise forms.ValidationError('Please enter their full name.')
        return name

    def save(self):
        email = self.cleaned_data['email']
        first, _, last = self.cleaned_data['full_name'].partition(' ')

        user = self.instance or User(username=email)
        user.email = email
        user.username = email
        user.first_name = first
        user.last_name = last.strip()

        if self.instance is None:
            user.set_unusable_password()

        user.save()

        profile = user.profile
        profile.role = self.cleaned_data['role']
        profile.save()
        profile.teams.set(self.cleaned_data['teams'])

        return user


class _PersonLabel:
    """Name and address together.

    Two colleagues can share a name, and putting the wrong one in a team is a
    permissions mistake. The address is what tells them apart.
    """

    def label_from_instance(self, obj):
        name = obj.get_full_name()
        return f'{name} ({obj.email})' if name and obj.email else (name or obj.get_username())


class LeadField(_PersonLabel, forms.ModelChoiceField):
    pass


class MembersField(_PersonLabel, forms.ModelMultipleChoiceField):
    pass


class TeamForm(forms.ModelForm):
    """Create or rename a team, set its lead, and pick who sits in it."""

    members = MembersField(
        queryset=User.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Whose work this team lead reviews. Someone may sit in more than one team.',
    )

    class Meta:
        model = Team
        fields = ['name', 'lead']
        field_classes = {'lead': LeadField}
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Account Services'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # limit_choices_to on the model already restricts the lead dropdown to
        # active leadership accounts. Order it so it is usable.
        self.fields['lead'].queryset = self.fields['lead'].queryset.order_by(
            'first_name', 'username',
        )
        self.fields['lead'].empty_label = 'Choose a lead…'

        self.fields['members'].queryset = User.objects.filter(is_active=True).order_by(
            'first_name', 'username',
        )
        if self.instance.pk:
            self.fields['members'].initial = User.objects.filter(profile__teams=self.instance)

    def clean_name(self):
        name = self.cleaned_data['name'].strip()

        clash = Team.objects.filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('A team with that name already exists.')

        return name

    def save(self, commit=True):
        team = super().save(commit=commit)

        members = set(self.cleaned_data['members'])

        # The lead goes in whether or not they were ticked. A lead who is not a
        # member cannot be assigned work in their own team, because the task
        # form checks the assignee belongs to the chosen team.
        members.add(team.lead)

        for person in members:
            person.profile.teams.add(team)

        for person in User.objects.filter(profile__teams=team).exclude(
            pk__in=[p.pk for p in members]
        ):
            person.profile.teams.remove(team)

        return team
