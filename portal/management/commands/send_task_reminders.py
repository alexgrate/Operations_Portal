"""Email assignees about tasks approaching or past their deadline.

Meant to be run by cron every few minutes. Safe to run as often as you like:
the rules in portal/reminders.py decide what is actually due, so extra runs
send nothing. See the README for the cron line.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal import reminders


class Command(BaseCommand):
    help = 'Send deadline reminders for open tasks.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would be sent without sending anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()

        if not settings.REMINDERS_ENABLED and not dry_run:
            self.stdout.write('Reminders are switched off (REMINDERS_ENABLED).')
            return

        sent = failed = 0

        for task in reminders.candidates():
            kind = reminders.due_kind(task, now)
            if kind is None:
                continue

            subject, text, html = reminders.build_email(task, kind, now)

            if dry_run:
                self.stdout.write(f'  [{kind}] {task.assignee.email}: {subject}')
                sent += 1
                continue

            try:
                message = EmailMultiAlternatives(
                    subject=subject,
                    body=text,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[task.assignee.email],
                )
                message.attach_alternative(html, 'text/html')
                message.send(fail_silently=False)
            except Exception as exc:
                # Leave the task unmarked so the next run tries again.
                failed += 1
                self.stderr.write(f'Task {task.pk} to {task.assignee.email} failed: {exc}')
                continue

            reminders.record_sent(task, kind, now)
            sent += 1

        verb = 'would send' if dry_run else 'sent'
        summary = f'{verb} {sent}'
        if failed:
            summary += f', {failed} failed'
        self.stdout.write(summary)
