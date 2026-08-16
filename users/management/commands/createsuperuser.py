"""Restrict `manage.py createsuperuser` to corporate email addresses.

Placing this module at users/management/commands/createsuperuser.py shadows
Django's built-in command, so `manage.py createsuperuser` runs this instead.

The pre_save signal in users/models.py is what actually guarantees the rule -
this subclass exists so the command fails with a readable message and
re-prompts, rather than blowing up with a ValidationError traceback.
"""
from django.contrib.auth.management.commands import createsuperuser
from django.core.management import CommandError

from users.models import CORPORATE_DOMAIN


class Command(createsuperuser.Command):
    help = f'Create a superuser, restricted to {CORPORATE_DOMAIN} email addresses.'

    @staticmethod
    def _is_corporate(value):
        return (value or '').strip().lower().endswith(CORPORATE_DOMAIN)

    def get_input_data(self, field, message, default=None):
        """Interactive path. Returning None makes Django ask again."""
        value = super().get_input_data(field, message, default)

        if field.name == 'email' and not self._is_corporate(value):
            self.stderr.write(f'Error: the email must end with {CORPORATE_DOMAIN}')
            return None

        return value

    def handle(self, *args, **options):
        """Non-interactive path (--noinput / --email passed on the command line)."""
        email = options.get('email')
        if email is not None and not self._is_corporate(email):
            raise CommandError(f'The email must end with {CORPORATE_DOMAIN}, got: {email}')

        return super().handle(*args, **options)
