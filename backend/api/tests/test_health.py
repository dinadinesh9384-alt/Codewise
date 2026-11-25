"""Tests for the versioned health endpoint."""
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient
from apps.repositories.models import User, Workspace, WorkspaceMembership


class HealthEndpointTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_health_returns_structured_operational_status(self):
        response = self.client.get(
            "/api/v1/health/",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "codewise-backend",
                "api_version": "v1",
            },
        )

    def test_health_returns_json_when_html_is_accepted(self):
        response = self.client.get(
            "/api/v1/health/",
            HTTP_ACCEPT="text/html",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "codewise-backend",
                "api_version": "v1",
            },
        )

    def test_health_response_contains_no_environment_or_secret_fields(self):
        response = self.client.get("/api/v1/health/")
        payload = response.json()

        self.assertEqual(set(payload), {"status", "service", "api_version"})
        self.assertNotIn("SECRET_KEY", response.content.decode("utf-8"))
        self.assertNotIn("DJANGO_", response.content.decode("utf-8"))

    def test_csrf_bootstrap_is_public_and_sets_the_session_post_cookie(self):
        response = self.client.get("/api/v1/session/csrf/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["csrfToken"])
        self.assertIn("csrftoken", response.cookies)


class SessionCsrfIntegrationTests(TestCase):
    def test_session_post_uses_json_token_when_cookie_is_http_only(self):
        user = User.objects.create_user(username="csrf-integration", password="pass")
        workspace = Workspace.objects.create(name="CSRF integration")
        WorkspaceMembership.objects.create(workspace=workspace, user=user, role="owner")
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        url = f"/api/v1/workspaces/{workspace.id}/submissions/"
        payload = {"repository_url": "http://127.0.0.1/private"}

        denied = client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="csrf-required")
        self.assertEqual(denied.status_code, 403)

        bootstrap = client.get("/api/v1/session/csrf/")
        self.assertEqual(bootstrap.status_code, 200)
        self.assertTrue(bootstrap.cookies["csrftoken"]["httponly"])
        accepted_by_csrf = client.post(url, payload, format="json",
            HTTP_IDEMPOTENCY_KEY="csrf-json-token", HTTP_X_CSRFTOKEN=bootstrap.json()["csrfToken"])
        self.assertEqual(accepted_by_csrf.status_code, 400)
