from django import forms
from django.contrib.auth.models import User

from .models import CORPORATE_DOMAIN, ROLE_ADMIN, Profile, Team, assignable_roles


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


def _operational(people):
    """Drop Admin accounts from a picker of people.

    Admin is a setup role, not an operational one. is_dept_head() counts the
    Admin role and any superuser as a head, so an admin leading a team becomes
    its reviewer, and an admin sitting in one becomes assignable within it -
    the same distortion of the sign-off chain that keeps them out of the task
    assignee list.

    Both exclusions are needed: the role covers an Admin who is not a
    superuser, is_superuser covers a superuser whose role was later changed.
    """
    return people.exclude(profile__role=ROLE_ADMIN).exclude(is_superuser=True)


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
        # active leadership accounts, but LEADERSHIP_ROLES includes Admin.
        # Drop those here - see _operational. Whoever already leads this team
        # stays selectable, or editing its name would fail validation on a
        # field the page was not offering to change.
        leads = _operational(self.fields['lead'].queryset)
        if self.instance.pk and self.instance.lead_id:
            leads = leads | self.fields['lead'].queryset.filter(pk=self.instance.lead_id)

        self.fields['lead'].queryset = leads.distinct().order_by(
            'first_name', 'username',
        )
        self.fields['lead'].empty_label = 'Choose a lead…'

        members = _operational(User.objects.filter(is_active=True))

        # An admin already sitting in this team stays listed. Hiding a current
        # member would not merely omit the row: save() below removes everybody
        # left unticked, so they would be dropped from the team silently by an
        # edit that never mentioned them.
        #
        # Deliberately still active-only, which leaves deactivated members
        # behaving exactly as they did before: this guard exists to cover the
        # people the filter above newly hides, and nobody else.
        if self.instance.pk:
            current = User.objects.filter(profile__teams=self.instance)
            members = members | current.filter(is_active=True)
            self.fields['members'].initial = current

        self.fields['members'].queryset = members.distinct().order_by(
            'first_name', 'username',
        )

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
