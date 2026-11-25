"""URL routes for API version 1."""
from django.urls import path

from .views import (AgentResultsView, CsrfTokenView, HealthView, HumanApprovalView, PendingProposalsView,
                    RepositorySubmissionDetailView, RepositorySubmissionsView,
                    ReadinessView, SessionLoginView, SessionLogoutView, SessionView,
                    WorkflowExecutionDetailView, WorkflowExecutionsView, WorkspaceListView)

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("ready/", ReadinessView.as_view(), name="readiness"),
    path("session/csrf/", CsrfTokenView.as_view(), name="csrf-token"),
    path("session/", SessionView.as_view(), name="session"),
    path("session/login/", SessionLoginView.as_view(), name="session-login"),
    path("session/logout/", SessionLogoutView.as_view(), name="session-logout"),
    path("workspaces/", WorkspaceListView.as_view(), name="workspaces"),
    path("workspaces/<uuid:workspace_id>/submissions/", RepositorySubmissionsView.as_view(), name="submissions"),
    path("workspaces/<uuid:workspace_id>/submissions/<uuid:submission_id>/",
         RepositorySubmissionDetailView.as_view(), name="submission-detail"),
    path("workspaces/<uuid:workspace_id>/executions/", WorkflowExecutionsView.as_view(), name="executions"),
    path("workspaces/<uuid:workspace_id>/executions/<uuid:execution_id>/",
         WorkflowExecutionDetailView.as_view(), name="execution-detail"),
    path("workspaces/<uuid:workspace_id>/executions/<uuid:execution_id>/agents/",
         AgentResultsView.as_view(), name="execution-agents"),
    path("workspaces/<uuid:workspace_id>/executions/<uuid:execution_id>/approval-proposals/",
         PendingProposalsView.as_view(), name="approval-proposals"),
    path("workspaces/<uuid:workspace_id>/executions/<uuid:execution_id>/approval/",
         HumanApprovalView.as_view(), name="execution-approval"),
]
