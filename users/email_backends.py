"""Send email through Microsoft Graph.

Office 365 disables SMTP AUTH, so this uses the client-credentials flow.
The app registration needs the Mail.Send application permission.
"""

import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


def _ssl_context():
    """TLS context, falling back to certifi when the system store is empty."""
    context = ssl.create_default_context()
    if context.cert_store_stats().get('x509_ca', 0):
        return context
    try:
        import certifi
    except ImportError:
        return context
    return ssl.create_default_context(cafile=certifi.where())

TOKEN_URL = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
SENDMAIL_URL = 'https://graph.microsoft.com/v1.0/users/{sender}/sendMail'
SCOPE = 'https://graph.microsoft.com/.default'

EXPIRY_SKEW_SECONDS = 120


class GraphEmailError(Exception):
    """Raised when Graph rejects a token request or a message."""


class MicrosoftGraphEmailBackend(BaseEmailBackend):
    _token = None
    _token_expires_at = 0.0
    _token_lock = threading.Lock()

    def __init__(self, fail_silently=False, timeout=None, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.tenant_id = getattr(settings, 'MS_GRAPH_TENANT_ID', '')
        self.client_id = getattr(settings, 'MS_GRAPH_CLIENT_ID', '')
        self.client_secret = getattr(settings, 'MS_GRAPH_CLIENT_SECRET', '')
        self.sender = getattr(settings, 'MS_GRAPH_SENDER', '')
        self.timeout = timeout if timeout is not None else 15
        self.ssl_context = _ssl_context()


    def _get_token(self):
        now = time.monotonic()
        cls = type(self)

        with cls._token_lock:
            if cls._token and now < cls._token_expires_at:
                return cls._token

            payload = urllib.parse.urlencode({
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': SCOPE,
                'grant_type': 'client_credentials',
            }).encode()

            request = urllib.request.Request(
                TOKEN_URL.format(tenant=urllib.parse.quote(self.tenant_id)),
                data=payload,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                method='POST',
            )

            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self.ssl_context
                ) as response:
                    body = json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors='replace')
                raise GraphEmailError(
                    f'Microsoft Graph rejected the token request ({exc.code}): {detail}'
                ) from exc
            except urllib.error.URLError as exc:
                raise GraphEmailError(f'Could not reach Microsoft Graph: {exc.reason}') from exc

            token = body.get('access_token')
            if not token:
                raise GraphEmailError(f'No access_token in Graph response: {body}')

            cls._token = token
            cls._token_expires_at = now + int(body.get('expires_in', 3600)) - EXPIRY_SKEW_SECONDS
            return token


    @staticmethod
    def _recipients(addresses):
        return [{'emailAddress': {'address': address}} for address in addresses if address]

    def _build_payload(self, message):
        body_type, body_content = 'Text', message.body

        for content, mimetype in getattr(message, 'alternatives', []) or []:
            if mimetype == 'text/html':
                body_type, body_content = 'HTML', content
                break

        payload = {
            'message': {
                'subject': message.subject,
                'body': {'contentType': body_type, 'content': body_content},
                'toRecipients': self._recipients(message.to),
            },
            'saveToSentItems': False,
        }

        if message.cc:
            payload['message']['ccRecipients'] = self._recipients(message.cc)
        if message.bcc:
            payload['message']['bccRecipients'] = self._recipients(message.bcc)
        if message.reply_to:
            payload['message']['replyTo'] = self._recipients(message.reply_to)

        return payload

    def _post_message(self, token, message):
        request = urllib.request.Request(
            SENDMAIL_URL.format(sender=urllib.parse.quote(self.sender)),
            data=json.dumps(self._build_payload(message)).encode(),
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=self.ssl_context
            ) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors='replace')
            raise GraphEmailError(f'Graph refused the message ({exc.code}): {detail}') from exc
        except urllib.error.URLError as exc:
            raise GraphEmailError(f'Could not reach Microsoft Graph: {exc.reason}') from exc

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        missing = [
            name for name, value in (
                ('MS_GRAPH_TENANT_ID', self.tenant_id),
                ('MS_GRAPH_CLIENT_ID', self.client_id),
                ('MS_GRAPH_CLIENT_SECRET', self.client_secret),
                ('MS_GRAPH_SENDER', self.sender),
            ) if not value
        ]
        if missing:
            error = GraphEmailError(f'Microsoft Graph is not configured: {", ".join(missing)} unset')
            if not self.fail_silently:
                raise error
            logger.error('%s', error)
            return 0

        try:
            token = self._get_token()
        except GraphEmailError:
            if not self.fail_silently:
                raise
            logger.exception('Could not get a Microsoft Graph token')
            return 0

        sent = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                if self._post_message(token, message):
                    sent += 1
            except GraphEmailError:
                if not self.fail_silently:
                    raise
                logger.exception('Could not send a message through Microsoft Graph')
        return sent
