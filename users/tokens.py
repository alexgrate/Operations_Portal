"""Invite links: the password-reset mechanism with a longer expiry."""
from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int


class InviteTokenGenerator(PasswordResetTokenGenerator):
    @property
    def timeout(self):
        return getattr(settings, 'INVITE_LINK_TIMEOUT', 7 * 24 * 60 * 60)

    def check_token(self, user, token):
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split('-')
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False

        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False

        return (self._num_seconds(self._now()) - ts) <= self.timeout


invite_token_generator = InviteTokenGenerator()
