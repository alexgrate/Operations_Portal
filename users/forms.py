from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db.models import Q


class SignUpForm(UserCreationForm):
    """Registration form for Dash MFB staff.

    The portal signs people in with their corporate email, so the email is
    stored as the username as well as on User.email.
    """

    full_name = forms.CharField(
        max_length=150,
        label='Full name',
        widget=forms.TextInput(attrs={'placeholder': 'Tunde Bakare', 'autocomplete': 'name'}),
    )
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={
            'placeholder': 'name.surname@dash-mfb.com',
            'autocomplete': 'email',
        }),
    )

    class Meta:
        model = User
        fields = ('email',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({
            'placeholder': 'At least 8 characters',
            'autocomplete': 'new-password',
        })
        self.fields['password2'].widget.attrs.update({
            'placeholder': 'Re-enter your password',
            'autocomplete': 'new-password',
        })
        self.fields['password1'].label = 'Password'
        self.fields['password2'].label = 'Confirm password'

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if len(email) > 150:
            raise forms.ValidationError('That email address is too long.')
        if User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean_full_name(self):
        full_name = self.cleaned_data['full_name'].strip()
        if not full_name:
            raise forms.ValidationError('Please enter your full name.')
        return full_name

    def save(self, commit=True):
        user = super().save(commit=False)
        email = self.cleaned_data['email']
        user.email = email
        user.username = email

        first, _, last = self.cleaned_data['full_name'].partition(' ')
        user.first_name = first
        user.last_name = last.strip()

        if commit:
            user.save()
        return user
