"""Production entry point. This is what NSSM runs as a Windows service.

Never `manage.py runserver` - that is a development server, single-threaded
and explicitly not built to face traffic.

Listens on the loopback only. IIS is the front door; nothing should be able to
reach waitress directly from the network.
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Operations_Portal.settings')

from waitress import serve  # noqa: E402

from Operations_Portal.wsgi import application  # noqa: E402

if __name__ == '__main__':
    serve(
        application,
        host='127.0.0.1',
        port=int(os.environ.get('PORT', '8000')),
        threads=int(os.environ.get('WAITRESS_THREADS', '8')),

        # Waitress DISCARDS X-Forwarded-* headers unless it is told which peer
        # is allowed to set them, because anyone who can reach it could
        # otherwise claim their request arrived over HTTPS.
        #
        # Without these three lines Django never sees X-Forwarded-Proto, so it
        # believes every request is plain HTTP, SECURE_SSL_REDIRECT bounces it
        # to HTTPS, IIS proxies it back, and the browser gives up on a
        # redirect loop. Safe here only because the listener is bound to the
        # loopback, so 127.0.0.1 really is IIS and nothing else.
        trusted_proxy='127.0.0.1',
        trusted_proxy_count=1,
        trusted_proxy_headers={'x-forwarded-proto', 'x-forwarded-host', 'x-forwarded-for'},

        # IIS is already the reverse proxy, so let it own the client-facing
        # timeouts and keep waitress from holding sockets open behind it.
        channel_timeout=120,
        ident='Operations Portal',
    )
