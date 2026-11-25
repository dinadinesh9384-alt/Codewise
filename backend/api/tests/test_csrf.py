"""CSRF failure classification: stable JSON codes, no leaked secrets, DEBUG-independent."""
import json

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from api.csrf import (CsrfAwareSessionAuthentication, CsrfFailed, classify_csrf_reason, csrf_failure)
from api.v1 import views
from apps.repositories.models import User, Workspace, WorkspaceMembership

CSRF_FAILED = {"error": "csrf_failed"}
LOGIN_URL = "/api/v1/session/login/"
LOGOUT_URL = "/api/v1/session/logout/"
CSRF_URL = "/api/v1/session/csrf/"
SENTINEL_TOKEN = "sentinel-token-value-that-must-never-leak"
SENTINEL_COOKIE = "sentinel-cookie-value-that-must-never-leak"
SENTINEL_ORIGIN = "https://evil-origin-sentinel.example"


def _assert_json_csrf_failed(testcase, response):
    testcase.assertEqual(response.status_code, 403)
    testcase.assertEqual(response["Content-Type"], "application/json")
    testcase.assertEqual(response.json(), CSRF_FAILED)


class CsrfSettingsWiringTests(SimpleTestCase):
    def test_failure_view_is_configured(self):
        self.assertEqual(settings.CSRF_FAILURE_VIEW, "api.csrf.csrf_failure")
        self.assertIn("django.middleware.csrf.CsrfViewMiddleware", settings.MIDDLEWARE)

    def test_session_views_use_the_csrf_aware_authentication_class(self):
        self.assertEqual(views.AuthenticatedAPIView.authentication_classes, [CsrfAwareSessionAuthentication])
        self.assertEqual(views.SessionView.authentication_classes, [CsrfAwareSessionAuthentication])

    def test_reason_classification_uses_fixed_categories_only(self):
        from django.middleware.csrf import (REASON_BAD_ORIGIN, REASON_BAD_REFERER, REASON_CSRF_TOKEN_MISSING,
                                            REASON_NO_CSRF_COOKIE, REASON_NO_REFERER)
        self.assertEqual(classify_csrf_reason(REASON_NO_CSRF_COOKIE), "cookie_missing")
        self.assertEqual(classify_csrf_reason(REASON_CSRF_TOKEN_MISSING), "token_missing")
        self.assertEqual(classify_csrf_reason(REASON_BAD_ORIGIN % SENTINEL_ORIGIN), "origin_rejected")
        self.assertEqual(classify_csrf_reason(REASON_BAD_REFERER % SENTINEL_ORIGIN), "referer_rejected")
        self.assertEqual(classify_csrf_reason(REASON_NO_REFERER), "referer_rejected")
        self.assertEqual(classify_csrf_reason("CSRF token from the 'X-Csrftoken' HTTP header has incorrect length."),
                         "token_invalid")
        self.assertEqual(classify_csrf_reason("anything else"), "other")
        self.assertEqual(classify_csrf_reason(None), "other")

    def test_csrf_failed_exception_renders_the_stable_body(self):
        self.assertEqual(CsrfFailed().status_code, 403)
        self.assertEqual(dict(CsrfFailed().detail), CSRF_FAILED)
        self.assertEqual(csrf_failure.__name__, "csrf_failure")


class CsrfFailureResponseTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = User.objects.create_user(username="csrf-user", password="correct-horse")
        self.workspace = Workspace.objects.create(name="Visible")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=self.user, role="member")
        self.other_user = User.objects.create_user(username="csrf-other", password="correct-horse")
        self.hidden = Workspace.objects.create(name="Hidden")
        WorkspaceMembership.objects.create(workspace=self.hidden, user=self.other_user, role="owner")

    def _token(self):
        response = self.client.get(CSRF_URL)
        self.assertEqual(response.status_code, 200)
        return response.json()["csrfToken"]

    def _login(self):
        token = self._token()
        response = self.client.post(LOGIN_URL, {"username": "csrf-user", "password": "correct-horse"},
                                    format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        return response.json()["csrfToken"]

    # --- Django middleware path (login uses @csrf_protect) -------------------------------
    def test_login_post_without_csrf_token_returns_json_csrf_failed(self):
        response = self.client.post(LOGIN_URL, {"username": "csrf-user", "password": "correct-horse"}, format="json")
        _assert_json_csrf_failed(self, response)

    def test_login_post_with_invalid_csrf_token_returns_json_csrf_failed(self):
        self._token()
        response = self.client.post(LOGIN_URL, {"username": "csrf-user", "password": "correct-horse"},
                                    format="json", HTTP_X_CSRFTOKEN="x" * 64)
        _assert_json_csrf_failed(self, response)

    def test_login_post_with_malformed_csrf_token_returns_json_csrf_failed(self):
        self._token()
        response = self.client.post(LOGIN_URL, {"username": "csrf-user", "password": "correct-horse"},
                                    format="json", HTTP_X_CSRFTOKEN="short")
        _assert_json_csrf_failed(self, response)

    def test_login_failure_is_not_a_csrf_failure_when_the_token_is_valid(self):
        token = self._token()
        response = self.client.post(LOGIN_URL, {"username": "nobody", "password": "wrong"}, format="json",
                                    HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "authentication_failed"})

    # --- DRF SessionAuthentication path -------------------------------------------------
    def test_authenticated_post_without_csrf_token_returns_recognizable_csrf_error(self):
        self._login()
        response = self.client.post(LOGOUT_URL, {}, format="json")
        _assert_json_csrf_failed(self, response)

    def test_authenticated_post_with_invalid_csrf_token_returns_recognizable_csrf_error(self):
        self._login()
        response = self.client.post(LOGOUT_URL, {}, format="json", HTTP_X_CSRFTOKEN="y" * 64)
        _assert_json_csrf_failed(self, response)

    def test_workspace_post_without_csrf_token_returns_recognizable_csrf_error(self):
        self._login()
        url = f"/api/v1/workspaces/{self.workspace.id}/submissions/"
        response = self.client.post(url, {"repository_url": "https://github.com/o/r"}, format="json",
                                    HTTP_IDEMPOTENCY_KEY="csrf-required")
        _assert_json_csrf_failed(self, response)

    # --- Authentication / permission errors stay distinct ------------------------------------
    def test_unauthenticated_requests_remain_authentication_errors_not_csrf_errors(self):
        for method, url in (("post", LOGOUT_URL), ("get", "/api/v1/workspaces/")):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, **({"data": {}, "format": "json"} if method == "post" else {}))
                self.assertEqual(response.status_code, 403)
                body = response.json()
                self.assertNotEqual(body, CSRF_FAILED)
                self.assertNotIn("error", body)
                self.assertEqual(body["detail"], "Authentication credentials were not provided.")

    def test_unauthenticated_post_with_a_valid_csrf_token_is_still_an_authentication_error(self):
        token = self._token()
        response = self.client.post(LOGOUT_URL, {}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Authentication credentials were not provided.")

    def test_workspace_permission_error_keeps_its_own_code_and_is_not_csrf(self):
        token = self._login()
        url = f"/api/v1/workspaces/{self.hidden.id}/submissions/"
        response = self.client.post(url, {"repository_url": "https://github.com/o/r"}, format="json",
                                    HTTP_IDEMPOTENCY_KEY="perm-1", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "workspace_access_denied"})

    # --- Valid requests still succeed ---------------------------------------------------
    def test_valid_csrf_protected_requests_still_succeed(self):
        token = self._login()
        self.assertEqual(self.client.get("/api/v1/workspaces/").status_code, 200)
        response = self.client.post(LOGOUT_URL, {}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"is_authenticated": False})

    # --- No leakage -----------------------------------------------------------------------
    def test_failure_responses_and_logs_do_not_expose_tokens_cookies_or_origin_values(self):
        # Middleware path (login): bad token, bad origin, sentinel cookie.
        self.client.cookies["csrftoken"] = SENTINEL_COOKIE
        with self.assertLogs("codewise.security.csrf", level="WARNING") as middleware_logs:
            middleware = self.client.post(LOGIN_URL, {"username": "a", "password": "b"}, format="json",
                                          HTTP_X_CSRFTOKEN=SENTINEL_TOKEN, HTTP_ORIGIN=SENTINEL_ORIGIN)
        _assert_json_csrf_failed(self, middleware)

        # DRF path (logout): log in with a valid token first, then send sentinel values.
        self.client.cookies.pop("csrftoken", None)
        valid_token = self._login()
        session_id = self.client.cookies["sessionid"].value
        self.client.cookies["csrftoken"] = SENTINEL_COOKIE
        with self.assertLogs("codewise.security.csrf", level="WARNING") as drf_logs:
            drf = self.client.post(LOGOUT_URL, {}, format="json", HTTP_X_CSRFTOKEN=SENTINEL_TOKEN,
                                   HTTP_ORIGIN=SENTINEL_ORIGIN)
        _assert_json_csrf_failed(self, drf)

        secrets = (SENTINEL_TOKEN, SENTINEL_COOKIE, SENTINEL_ORIGIN, "evil-origin-sentinel", valid_token, session_id)
        for text in (middleware.content.decode(), drf.content.decode(), *middleware_logs.output, *drf_logs.output):
            for secret in secrets:
                self.assertNotIn(secret, text)
        self.assertTrue(any("source=middleware" in line and "category=origin_rejected" in line
                            for line in middleware_logs.output))
        self.assertTrue(any("source=drf_session" in line and "category=origin_rejected" in line
                            for line in drf_logs.output))
        self.assertEqual(set(middleware.json()), {"error"})
        self.assertEqual(set(drf.json()), {"error"})

    def test_log_records_only_fixed_fields(self):
        with self.assertLogs("codewise.security.csrf", level="WARNING") as logs:
            self.client.post(LOGIN_URL + "?token=" + SENTINEL_TOKEN, {}, format="json")
        line = logs.output[0]
        self.assertIn("path=/api/v1/session/login/ ", line + " ")
        self.assertNotIn(SENTINEL_TOKEN, line)
        self.assertIn("cookie_present=False", line)
        self.assertIn("header_present=False", line)
        self.assertIn("category=cookie_missing", line)

    # --- DEBUG independence -------------------------------------------------------------------
    def test_failure_responses_are_identical_with_debug_enabled_and_disabled(self):
        self._login()
        bodies = {}
        for debug in (True, False):
            with self.subTest(debug=debug), override_settings(DEBUG=debug):
                middleware = self.client.post(LOGIN_URL, {"username": "a", "password": "b"}, format="json")
                drf = self.client.post(LOGOUT_URL, {}, format="json")
                _assert_json_csrf_failed(self, middleware)
                _assert_json_csrf_failed(self, drf)
                bodies[debug] = (middleware.content, drf.content)
        self.assertEqual(bodies[True], bodies[False])
        for content in bodies[True]:
            self.assertNotIn(b"Reason given for failure", content)
            self.assertNotIn(b"CSRF cookie not set", content)
