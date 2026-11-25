"""Tests for safe baseline backend configuration."""
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.repositories.models import User, Workspace, WorkspaceMembership


class BackendSettingsTests(SimpleTestCase):
    def test_drf_requires_authenticated_sessions_by_default(self):
        self.assertIn("rest_framework", settings.INSTALLED_APPS)
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"],
                         ["rest_framework.authentication.SessionAuthentication"])
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"],
            ["rest_framework.permissions.IsAuthenticated"],
        )

    def test_security_static_and_timezone_settings_are_configured(self):
        self.assertTrue(settings.SECRET_KEY)
        self.assertTrue(settings.ALLOWED_HOSTS)
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.X_FRAME_OPTIONS, "DENY")
        self.assertEqual(settings.TIME_ZONE, "UTC")
        self.assertTrue(settings.USE_TZ)
        self.assertEqual(settings.STATIC_URL, "/static/")
        self.assertEqual(settings.STATIC_ROOT.name, "staticfiles")

    def test_json_log_formatter_redacts_common_credential_fields(self):
        import json
        import logging
        from config.logging import JsonLogFormatter

        record = logging.LogRecord("codewise.test", logging.WARNING, __file__, 1,
            "Authorization: Bearer synthetic-token password=synthetic-password", (), None)
        payload = json.loads(JsonLogFormatter().format(record))
        self.assertEqual(payload["level"], "WARNING")
        self.assertNotIn("synthetic-token", payload["message"])
        self.assertNotIn("synthetic-password", payload["message"])


class SessionAndWorkspaceEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = User.objects.create_user(username="session-user", password="correct-horse")
        self.workspace = Workspace.objects.create(name="Visible")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=self.user, role="member")
        self.other_user = User.objects.create_user(username="other-session-user", password="correct-horse")
        self.hidden_workspace = Workspace.objects.create(name="Hidden")
        WorkspaceMembership.objects.create(workspace=self.hidden_workspace, user=self.other_user, role="owner")

    def _csrf(self):
        response = self.client.get("/api/v1/session/csrf/")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrfToken"]

    def test_session_login_requires_csrf_and_returns_generic_auth_failure(self):
        login_url = "/api/v1/session/login/"
        denied = self.client.post(login_url, {"username": "session-user", "password": "correct-horse"}, format="json")
        self.assertEqual(denied.status_code, 403)
        token = self._csrf()
        bad = self.client.post(login_url, {"username": "unknown", "password": "wrong"}, format="json",
            HTTP_X_CSRFTOKEN=token)
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.json(), {"error": "authentication_failed"})

    def test_login_workspace_discovery_and_logout_use_the_existing_session(self):
        token = self._csrf()
        response = self.client.post("/api/v1/session/login/",
            {"username": "session-user", "password": "correct-horse"}, format="json",
            HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_authenticated"])
        self.assertTrue(response.data["csrfToken"])
        session = self.client.get("/api/v1/session/")
        self.assertEqual(session.json()["username"], "session-user")

        workspaces = self.client.get("/api/v1/workspaces/")
        self.assertEqual(workspaces.status_code, 200)
        self.assertEqual(workspaces.data["workspaces"], [{"id": str(self.workspace.id),
            "name": "Visible", "role": "member"}])

        csrf_denied = self.client.post("/api/v1/session/logout/", {}, format="json")
        self.assertEqual(csrf_denied.status_code, 403)
        logged_out = self.client.post("/api/v1/session/logout/", {}, format="json",
            HTTP_X_CSRFTOKEN=response.data["csrfToken"])
        self.assertEqual(logged_out.status_code, 200)
        self.assertFalse(self.client.get("/api/v1/session/").json()["is_authenticated"])
        self.assertEqual(self.client.get("/api/v1/workspaces/").status_code, 403)

    def test_readiness_reports_only_safe_dependency_states(self):
        from django.test import override_settings

        response = self.client.get("/api/v1/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["checks"], {"database": "ok", "workflow_runtime": "unavailable",
                                                     "approval_checkpoint": "unavailable"})

        class FakeRuntime:
            durable_approval_supported = False
            def workflow_for(self, **kwargs):
                return None

        with override_settings(CODEWISE_API_WORKFLOW_FACTORY=FakeRuntime):
            ready = self.client.get("/api/v1/ready/")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.data["checks"]["approval_checkpoint"], "unavailable")
        self.assertNotIn("SECRET", ready.content.decode())
