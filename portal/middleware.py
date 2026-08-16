"""Turn stray ValidationErrors into a readable message instead of a 500.

Model-level validation (the corporate-email rule in users/models.py, for
instance) raises ValidationError from inside a signal. Nothing in the view
catches it, so without this it surfaces as a debug traceback in development
and a 500 page in production - neither of which tells the person what to fix.

Only unsafe methods are handled. A failing GET is left alone so it can render
the proper error page, and so a GET can never be redirected back onto itself.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})


class FriendlyValidationErrorMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, ValidationError):
            return None

        if request.method in SAFE_METHODS:
            return None

        if not hasattr(request, '_messages'):
            return None

        for message in exception.messages:
            messages.error(request, message)

        return redirect(self._safe_referer(request))

    @staticmethod
    def _safe_referer(request):
        """Back to the page they submitted from, as long as it's our own."""
        referer = request.META.get('HTTP_REFERER')
        if referer and url_has_allowed_host_and_scheme(
            referer,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return referer
        return '/'
