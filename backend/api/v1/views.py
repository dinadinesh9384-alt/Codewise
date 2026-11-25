"""Authenticated version 1 repository and workflow endpoints."""
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.middleware.csrf import get_token
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.http import Http404
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.repositories.errors import RepositoryGrantRequired, SnapshotNotSealed, WorkspaceAccessDenied
from apps.repositories.models import RepositorySubmission, WorkflowExecution, WorkspaceMembership
from apps.repositories.services import get_submission, require_workspace_membership
from .serializers import (ApprovalSerializer, RepositorySubmissionSerializer, SessionLoginSerializer,
                          WorkflowStartSerializer)
from .services import (APIServiceError, _configured_runtime, _idempotency_key, approve_execution,
                       start_execution, submit_repository)


class AuthenticatedAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def handle_exception(self, exc):
        if isinstance(exc, WorkspaceAccessDenied):
            return Response({"error": "workspace_access_denied"}, status=403)
        if isinstance(exc, (RepositoryGrantRequired, SnapshotNotSealed)):
            return Response({"error": "snapshot_access_unavailable"}, status=409)
        if isinstance(exc, APIServiceError):
            return Response({"error": exc.code}, status=exc.status)
        if isinstance(exc, ValidationError):
            return Response({"error": "invalid_request"}, status=400)
        if isinstance(exc, (APIException, Http404, PermissionDenied)):
            return super().handle_exception(exc)
        # Never serialize provider, database, or agent exception details.
        return Response({"error": "internal_error"}, status=500)


class HealthView(APIView):
    """Report process-level API availability without exposing environment details."""

    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    def perform_content_negotiation(self, request, force=False):
        """Always render this public health response as JSON, regardless of Accept."""
        renderer = self.get_renderers()[0]
        return renderer, renderer.media_type

    def get(self, request):
        return Response(
            {
                "status": "ok",
                "service": "codewise-backend",
                "api_version": "v1",
            }
        )


class CsrfTokenView(APIView):
    """Issue the CSRF cookie/token needed by session-authenticated browser clients."""

    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class SessionView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response({"is_authenticated": False})
        return Response({"is_authenticated": True, "user_id": str(user.pk), "username": user.get_username()})


class SessionLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    @method_decorator(csrf_protect)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        serializer = SessionLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, username=serializer.validated_data["username"],
                            password=serializer.validated_data["password"])
        if user is None:
            return Response({"error": "authentication_failed"}, status=401)
        login(request, user)
        return Response({"is_authenticated": True, "user_id": str(user.pk),
                         "username": user.get_username(), "csrfToken": get_token(request)})


class SessionLogoutView(AuthenticatedAPIView):
    def post(self, request):
        logout(request)
        return Response({"is_authenticated": False})


class WorkspaceListView(AuthenticatedAPIView):
    def get(self, request):
        rows = WorkspaceMembership.objects.filter(user=request.user).select_related("workspace").order_by(
            "workspace__name", "workspace_id")
        return Response({"workspaces": [{"id": str(row.workspace_id), "name": row.workspace.name,
                                          "role": row.role} for row in rows]})


class ReadinessView(APIView):
    """Check database and required workflow runtime without leaking dependency details."""
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            database = "ok"
        except Exception:
            # Missing drivers and connection failures are both unavailable dependencies.
            database = "unavailable"
        runtime = "unavailable"
        approval_checkpoint = "unavailable"
        try:
            configured_runtime = _configured_runtime()
            if configured_runtime is not None and callable(getattr(configured_runtime, "workflow_for", None)):
                runtime = "configured"
                approval_checkpoint = ("supported" if
                    getattr(configured_runtime, "durable_approval_supported", False) else "unavailable")
        except Exception:
            # Do not include exception or configuration contents in a public readiness response.
            pass
        ready = database == "ok" and runtime == "configured"
        return Response({"status": "ready" if ready else "unavailable",
                         "checks": {"database": database, "workflow_runtime": runtime,
                                    "approval_checkpoint": approval_checkpoint}},
                        status=200 if ready else 503)


def _submission_data(submission, snapshot=None):
    return {"id": str(submission.id), "workspace_id": str(submission.workspace_id),
        "repository_id": str(submission.repository_id) if submission.repository_id else None,
        "snapshot_id": str(snapshot.id) if snapshot else None, "status": submission.status,
        "error_code": submission.error_code or None, "created_at": submission.created_at.isoformat()}


def _execution_data(record):
    stored = record.result or {}
    agent_rows = stored.get("agents") or stored.get("agent_progress", ())
    return {"id": str(record.id), "submission_id": str(record.submission_id),
        "repository_id": str(record.binding.repository_id), "snapshot_id": str(record.binding.snapshot_id),
        "commit_sha": record.binding.commit_sha, "manifest_digest": record.binding.snapshot.manifest_digest,
        "status": record.status, "selected_agents": record.selected_agents,
        "selected_groups": record.selected_groups, "agent_statuses": [
            {"agent_id": item["agent_id"], "status": item["status"],
             "error_codes": item.get("error_codes") or ([item["error_code"]] if item.get("error_code") else [])}
            for item in agent_rows],
        "approval": stored.get("approval"),
        "final_review_status": stored.get("final_review_status"),
        "error_code": record.error_code or None, "created_at": record.created_at.isoformat(),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None}


class RepositorySubmissionsView(AuthenticatedAPIView):
    def post(self, request, workspace_id):
        serializer = RepositorySubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = _idempotency_key(request)
        submission, snapshot, created = submit_repository(user=request.user, workspace_id=workspace_id,
            payload=serializer.validated_data, key=key)
        return Response({"submission": _submission_data(submission, snapshot)}, status=201 if created else 200)


class RepositorySubmissionDetailView(AuthenticatedAPIView):
    def get(self, request, workspace_id, submission_id):
        submission = get_submission(user=request.user, workspace_id=workspace_id, submission_id=submission_id)
        if submission is None:
            return Response({"error": "submission_not_found"}, status=404)
        return Response({"submission": _submission_data(submission, submission.snapshot)})


class WorkflowExecutionsView(AuthenticatedAPIView):
    def post(self, request, workspace_id):
        serializer = WorkflowStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = _idempotency_key(request)
        record, created = start_execution(user=request.user, workspace_id=workspace_id,
            payload=serializer.validated_data, key=key)
        return Response({"execution": _execution_data(record)}, status=202 if created else 200)


class WorkflowExecutionDetailView(AuthenticatedAPIView):
    def get_record(self, user, workspace_id, execution_id):
        require_workspace_membership(user=user, workspace_id=workspace_id)
        return WorkflowExecution.objects.select_related("binding__snapshot", "binding__repository").filter(
            pk=execution_id, workspace_id=workspace_id).first()

    def get(self, request, workspace_id, execution_id):
        record = self.get_record(request.user, workspace_id, execution_id)
        if record is None:
            return Response({"error": "execution_not_found"}, status=404)
        return Response({"execution": _execution_data(record)})


class AgentResultsView(WorkflowExecutionDetailView):
    def get(self, request, workspace_id, execution_id):
        record = self.get_record(request.user, workspace_id, execution_id)
        if record is None:
            return Response({"error": "execution_not_found"}, status=404)
        stored = record.result or {}
        results = stored.get("agents") or stored.get("agent_progress", [])
        return Response({"execution_id": str(record.id), "agents": results})


class PendingProposalsView(WorkflowExecutionDetailView):
    def get(self, request, workspace_id, execution_id):
        record = self.get_record(request.user, workspace_id, execution_id)
        if record is None:
            return Response({"error": "execution_not_found"}, status=404)
        if record.status != WorkflowExecution.Status.PENDING_APPROVAL:
            return Response({"execution_id": str(record.id), "status": record.status, "proposals": []})
        agents = {item.get("agent_id"): item for item in (record.result or {}).get("agents", ())}
        patch = ((agents.get("patch_author") or {}).get("output") or {})
        review = ((agents.get("review_agent") or {}).get("output") or {})
        reviews = {row.get("proposal_id"): row for row in review.get("patch_reviews", ())}
        proposals = [{**item, "review": reviews.get(item.get("proposal_id"))}
                     for item in patch.get("proposals", ())]
        return Response({"execution_id": str(record.id), "status": record.status,
                         "final_review_status": (record.result or {}).get("final_review_status"),
                         "proposals": proposals})


class HumanApprovalView(WorkflowExecutionDetailView):
    def post(self, request, workspace_id, execution_id):
        serializer = ApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = approve_execution(user=request.user, workspace_id=workspace_id,
            execution_id=execution_id, payload=serializer.validated_data)
        return Response({"execution": _execution_data(record), "decision": serializer.validated_data["decision"]})
