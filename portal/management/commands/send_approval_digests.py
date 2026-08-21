"""Email each manager the work sitting waiting for their decision.

One email per person listing everything, not one per task. Meant to be run by
cron; see the README. Safe to run as often as you like, since the rules in
portal/digests.py decide who is actually due one.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal import digests


class Command(BaseCommand):
    help = 'Send each manager a digest of work awaiting their decision.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would be sent without sending anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()

        if not settings.APPROVAL_DIGESTS_ENABLED and not dry_run:
            self.stdout.write('Approval digests are switched off (APPROVAL_DIGESTS_ENABLED).')
            return

        sent = failed = 0

        for user in digests.approvers():
            pending = digests.waiting_on(user)
            if not digests.is_due(user, pending, now):
                continue

            subject, text, html = digests.build_email(user, pending, now)

            if dry_run:
                self.stdout.write(f'  {user.email}: {subject}')
                sent += 1
                continue

            try:
                message = EmailMultiAlternatives(
                    subject=subject,
                    body=text,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[user.email],
                )
                message.attach_alternative(html, 'text/html')
                message.send(fail_silently=False)
            except Exception as exc:
                # Leave the timestamp alone so the next run tries again.
                failed += 1
                self.stderr.write(f'Digest to {user.email} failed: {exc}')
                continue

            digests.record_sent(user, now)
            sent += 1

        verb = 'would send' if dry_run else 'sent'
        summary = f'{verb} {sent}'
        if failed:
            summary += f', {failed} failed'
        self.stdout.write(summary)
