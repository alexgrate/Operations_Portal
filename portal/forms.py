"""
Django Forms do two jobs, same as your JS validation did:
  1. Render the right HTML <input> for each field
  2. Validate submitted data BEFORE it ever touches the database

The difference from Option B: there, YOUR JavaScript validated
fields client-side and the browser sent JSON. Here, the browser
just submits a normal <form>, and Django validates it server-side
in Python — no fetch(), no JSON, no manual localStorage token.
"""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import ProcessType, Column, Task


class SignUpForm(UserCreationForm):
    """
    UserCreationForm is Django's BUILT-IN signup form — it already
    knows how to validate a password (matches confirmation, meets
    Django's password rules) without us writing that logic by hand,
    the way we did in signup.js's checkLogin().

    We add `full_name` and `email` ourselves because the built-in
    form only knows about `username` by default, and — same as
    before — our accounts use email AS the username.
    """
    full_name = forms.CharField(max_length=150, label='Full name')
    email = forms.EmailField(label='Email')

    class Meta:
        model = User
        # Only REAL model fields go here. full_name/password1/password2
        # are declared as form fields above/inherited — Django renders
        # them automatically without needing to be listed here.
        fields = ('email',)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email']
        user.email = self.cleaned_data['email']
        # Same name-splitting idea as the register API endpoint.
        parts = self.cleaned_data['full_name'].strip().split(' ', 1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ''
        if commit:
            user.save()
        return user


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'process_type', 'assignee', 'status']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'e.g. KYC review — Damola J'}),
        }


class ProcessTypeForm(forms.ModelForm):
    # `checklist` on the model is a JSONField (a list of strings) —
    # there's no natural HTML input for "a list." So we show a plain
    # textarea (one checklist item per line) and convert it to/from
    # a real list ourselves in the view, instead of trying to make
    # Django auto-generate a widget for it.
    checklist_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'One checklist item per line'}),
        label='Standard checklist (optional)',
    )

    class Meta:
        model = ProcessType
        fields = ['name', 'target_hours']


class ColumnForm(forms.ModelForm):
    class Meta:
        model = Column
        fields = ['name', 'starts', 'completes']
