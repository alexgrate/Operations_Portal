"""Forms for the task board and process catalog.

Each form renders the right HTML input for its fields and validates the
submitted data server-side, before anything reaches the database.

Account signup lives in users/forms.py, not here.
"""
from django import forms

from .models import Column, ProcessType, Task, Comment


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'process_type', 'assignee', 'status']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'e.g. KYC review — Damola J'}),
        }


class ProcessTypeForm(forms.ModelForm):
    checklist_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'One checklist item per line'}),
        label='Standard checklist (optional)',
    )

    class Meta:
        model = ProcessType
        fields = ['name', 'target_hours', 'requires_approval']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Account Opening — Retail'}),
            'target_hours': forms.NumberInput(attrs={'min': '0.5', 'step': '0.5'}),
        }

    def clean_target_hours(self):
        target_hours = self.cleaned_data['target_hours']
        if target_hours <= 0:
            raise forms.ValidationError('The turnaround target must be greater than zero hours.')
        return target_hours


class ColumnForm(forms.ModelForm):
    class Meta:
        model = Column
        fields = ['name', 'starts', 'completes']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. QA Sign-off'}),
        }


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['body']
        widgets = {'body': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a comment...'})}