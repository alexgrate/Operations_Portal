"""Single sign-on through OCI IAM (an OpenID Connect identity domain).

The authorization-code flow, with the client secret sent from the server:

    login page -> OCI sign-in -> /sso/callback/ -> code swapped for a token
               -> userinfo -> matched to an existing portal account

Nobody is created here. Accounts are still onboarded by an admin, so OCI only
answers "who is this"; whether they may use the portal is the portal's call.

Uses urllib rather than a new dependency, the same way the Graph email
backend does, and borrows its TLS context for the same certifi fallback.
"""
import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from .email_backends import _ssl_context

TIMEOUT_SECONDS = 10

# The discovery document changes rarely. Fetching it on every sign-in would
# put a round trip to Oracle in front of each login for nothing.
DISCOVERY_TTL_SECONDS = 60 * 60
_discovery_cache = {}


class SsoError(Exception):
    """Anything that should send the person back to the login page."""


def is_enabled():
    return bool(
        settings.OCI_SSO_ISSUER_URL
        and settings.OCI_SSO_CLIENT_ID
        and settings.OCI_SSO_CLIENT_SECRET
    )


def _request_json(request):
    try:
        with urllib.request.urlopen(
            request, timeout=TIMEOUT_SECONDS, context=_ssl_context()
        ) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')
        raise SsoError(f'OCI IAM refused the request ({exc.code}): {detail}') from exc
    except urllib.error.URLError as exc:
        raise SsoError(f'Could not reach OCI IAM: {exc.reason}') from exc
    except ValueError as exc:
        raise SsoError('OCI IAM sent back something that is not JSON.') from exc


def discover():
    """The identity domain's endpoints, from its OpenID configuration."""
    issuer = settings.OCI_SSO_ISSUER_URL.rstrip('/')
    cached = _discovery_cache.get(issuer)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    doc = _request_json(urllib.request.Request(
        f'{issuer}/.well-known/openid-configuration',
        headers={'Accept': 'application/json'},
    ))

    endpoints = {
        'authorize': doc.get('authorization_endpoint'),
        'token': doc.get('token_endpoint'),
        'userinfo': doc.get('userinfo_endpoint'),
    }
    # The client secret is posted to the token endpoint, so it must never be
    # plain HTTP, whatever the discovery document says.
    for name, url in endpoints.items():
        if not url or not url.startswith('https://'):
            raise SsoError(f'OCI IAM discovery gave no usable {name} endpoint.')

    _discovery_cache[issuer] = (time.monotonic() + DISCOVERY_TTL_SECONDS, endpoints)
    return endpoints


def new_attempt():
    """A fresh state value and PKCE pair for one sign-in attempt.

    The caller keeps both in the session. state ties the callback to the
    browser that started it, which is what stops someone sending a victim a
    callback link carrying the attacker's own code and signing them in as the
    attacker. PKCE means a code intercepted on the way back is useless alone.
    """
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return {
        'state': secrets.token_urlsafe(32),
        'verifier': verifier,
        'challenge': challenge,
    }


def auth_url(attempt, redirect_uri):
    """Where to send the browser to sign in at OCI."""
    params = urllib.parse.urlencode({
        'client_id': settings.OCI_SSO_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'scope': ' '.join(settings.OCI_SSO_SCOPES),
        'response_type': 'code',
        'state': attempt['state'],
        'code_challenge': attempt['challenge'],
        'code_challenge_method': 'S256',
    })
    return f"{discover()['authorize']}?{params}"


def exchange_code(code, verifier, redirect_uri):
    """Swap the one-time code for an access token, server to server."""
    basic = base64.b64encode(
        f'{settings.OCI_SSO_CLIENT_ID}:{settings.OCI_SSO_CLIENT_SECRET}'.encode()
    ).decode()

    body = _request_json(urllib.request.Request(
        discover()['token'],
        data=urllib.parse.urlencode({
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'code_verifier': verifier,
        }).encode(),
        headers={
            'Authorization': f'Basic {basic}',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
        },
        method='POST',
    ))

    token = body.get('access_token')
    if not token:
        raise SsoError('OCI IAM returned no access token.')
    return token


def user_profile(access_token):
    """Who OCI says this is.

    The token came straight from the token endpoint over TLS, so userinfo is
    asked rather than the ID token being decoded - that would need a JWT
    library and the domain's signing keys for no extra assurance here.
    """
    info = _request_json(urllib.request.Request(
        discover()['userinfo'],
        headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
    ))

    if not info.get('sub'):
        raise SsoError('OCI IAM userinfo carried no subject.')

    email = info.get('email') or info.get('preferred_username') or info.get('upn') or ''
    return {
        'provider_id': info['sub'],
        'email': email.strip().lower(),
        'first_name': info.get('given_name', ''),
        'last_name': info.get('family_name', ''),
    }
