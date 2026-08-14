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


# class ProcessTypeForm(forms.ModelForm):
#     checklist_text = forms.CharField(
#         required=False,
#         widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'One checklist item per line'}),
#         label='Standard checklist (optional)',
#     )

#     class Meta:
#         model = ProcessType
#         fields = ['name', 'target_hours', 'requires_approval']
#         widgets = {
#             'name': forms.TextInput(attrs={'placeholder': 'e.g. Account Opening — Retail'}),
#             'target_hours': forms.NumberInput(attrs={'min': '0.5', 'step': '0.5'}),
#         }

#     def clean_target_hours(self):
#         target_hours = self.cleaned_data['target_hours']
#         if target_hours <= 0:
#             raise forms.ValidationError('The turnaround target must be greater than zero hours.')
#         return target_hours


class ProcessTypeForm(forms.ModelForm):
    TARGET_UNIT_CHOICES = [('hours', 'Hours'), ('days', 'Days')]

    target_value = forms.FloatField(label='Default turnaround target', min_value=0.5)
    target_unit = forms.ChoiceField(choices=TARGET_UNIT_CHOICES, initial='hours', label='Unit')
    checklist_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'One checklist item per line'}),
        label='Standard checklist (optional)',
    )

    class Meta:
        model = ProcessType
        fields = ['name', 'requires_approval']  # target_hours is handled manually below, not auto-mapped

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Editing an existing one — pre-fill from what's stored.
            # Note: we always stored in hours, so editing always shows
            # hours back, even if it was originally entered as days.
            self.fields['target_value'].initial = self.instance.target_hours
            self.fields['checklist_text'].initial = '\n'.join(self.instance.checklist or [])

class ColumnForm(forms.ModelForm):
    class Meta:
        model = Column
        fields = ['name', 'starts', 'completes']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. QA Sign-off'}),
        }

def _apply_target_and_checklist(form, process_type):
    value = form.cleaned_data['target_value']
    unit = form.cleaned_data['target_unit']
    process_type.target_hours = value * 24 if unit == 'days' else value

    checklist_text = form.cleaned_data.get('checklist_text', '')
    process_type.checklist = [line.strip() for line in checklist_text.splitlines() if line.strip()]

# -------- New data end-------------------------------------

class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['body']
        widgets = {'body': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a comment...'})}