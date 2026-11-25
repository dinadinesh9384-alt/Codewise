"""Stable, non-leaking CSRF failure handling for the JSON API.

Two code paths reject CSRF in this backend, and both must surface the same stable error
code so browser clients never have to match English text or guess from HTML:

* ``CsrfViewMiddleware`` / ``@csrf_protect`` (used by the login endpoint) call
  ``CSRF_FAILURE_VIEW``. Django's default view renders an HTML 403 with no stable marker.
* DRF wraps every view in ``csrf_exempt`` and enforces CSRF itself inside
  ``SessionAuthentication.enforce_csrf``, which raises a ``PermissionDenied`` whose only
  identifier is the English prefix ``"CSRF Failed:"``.
"""
import logging

from django.http import JsonResponse
from django.middleware.csrf import (
    REASON_CSRF_TOKEN_MISSING,
    REASON_NO_CSRF_COOKIE,
)
from rest_framework import exceptions
from rest_framework.authentication import SessionAuthentication

CSRF_FAILED_CODE = "csrf_failed"
_DRF_REASON_PREFIX = "CSRF Failed: "

logger = logging.getLogger("codewise.security.csrf")


def classify_csrf_reason(reason):
    """Map a Django CSRF reason to a coarse, fixed category.

    Django's raw reasons can embed request-controlled values (for example the Origin or
    Referer header), so the raw string is never logged or returned.
    """
    reason = str(reason or "")
    if reason == REASON_NO_CSRF_COOKIE:
        return "cookie_missing"
    if reason == REASON_CSRF_TOKEN_MISSING:
        return "token_missing"
    if reason.startswith("Origin checking failed"):
        return "origin_rejected"
    if reason.startswith("Referer checking failed"):
        return "referer_rejected"
    if reason.startswith("CSRF token"):
        return "token_invalid"
    return "other"


def log_csrf_rejection(request, reason, source):
    """Log a rejection using only fixed categories, booleans and the path (no query string).

    Token values, cookie values, header values, the session id, and the raw Django reason
    are deliberately excluded.
    """
    logger.warning(
        "csrf_rejected source=%s category=%s method=%s path=%s cookie_present=%s header_present=%s origin_present=%s",
        source,
        classify_csrf_reason(reason),
        request.method,
        request.path,
        "csrftoken" in request.COOKIES,
        "X-CSRFToken" in request.headers,
        "Origin" in request.headers,
    )


def csrf_failure(request, reason=""):
    """``CSRF_FAILURE_VIEW``: identical JSON body regardless of ``DEBUG`` or the reason."""
    log_csrf_rejection(request, reason, source="middleware")
    return JsonResponse({"error": CSRF_FAILED_CODE}, status=403)


class CsrfFailed(exceptions.APIException):
    """DRF CSRF rejection with a stable machine-readable body: ``{"error": "csrf_failed"}``."""

    status_code = 403
    default_code = CSRF_FAILED_CODE
    default_detail = {"error": CSRF_FAILED_CODE}


class CsrfAwareSessionAuthentication(SessionAuthentication):
    """``SessionAuthentication`` whose CSRF rejection carries the stable ``csrf_failed`` code."""

    def enforce_csrf(self, request):
        try:
            super().enforce_csrf(request)
        except exceptions.PermissionDenied as exc:
            message = str(exc.detail)
            if not message.startswith(_DRF_REASON_PREFIX):
                raise
            log_csrf_rejection(request, message[len(_DRF_REASON_PREFIX):], source="drf_session")
            raise CsrfFailed() from None
