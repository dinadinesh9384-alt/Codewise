import ast
import pathlib
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from agent_foundation.errors import PermissionDeniedError, SnapshotUnavailableError
from agent_foundation.context import validate_relative_path
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.registry import AgentRegistry
from agent_foundation.permissions import Permission
from agent_foundation.context import ExecutionContext, SnapshotRepositoryReader
from agents.registry import register_structure_analyzer
from .support import module
from .test_structure_analyzer import MemoryReader, StructureAnalysisTests


class SecurityTests(SimpleTestCase):
    def test_path_traversal_and_absolute_paths_are_rejected(self):
        for path in ("../outside", "/etc/passwd", "C:/secret", "a\\..\\b"):
            with self.assertRaises(ValueError): validate_relative_path(path)

    def test_sensitive_content_is_not_returned_or_needed_for_analysis(self):
        reader = MemoryReader({".env.production":b"TOKEN=highly-sensitive-value", "README.md":b"# Repo\n"})
        request_helper = StructureAnalysisTests()
        context = request_helper.make_context()
        from agent_foundation.registry import AgentRegistry
        from agent_foundation.execution import ExecutionLifecycle
        from agents.registry import register_structure_analyzer
        registry = AgentRegistry()
        register_structure_analyzer(registry, reader)
        result = ExecutionLifecycle(registry).execute(request_helper.request(context), context)
        self.assertNotIn("highly-sensitive-value", result.output.model_dump_json())
        self.assertIn("sensitive_content_intentionally_skipped", {item.reason for item in result.output.quality.skipped_files})

    def test_no_model_provider_imports_or_code_execution_in_agent_modules(self):
        base = pathlib.Path(__file__).resolve().parents[1]
        for path in base.rglob("*.py"):
            if "tests" in path.parts: continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                    for name in names: self.assertFalse(name.startswith(("langchain", "langgraph", "llm_gateway", "github", "subprocess", "apps.repositories.storage")))
                elif isinstance(node, ast.ImportFrom):
                    name = node.module or ""
                    self.assertFalse(name.startswith(("langchain", "langgraph", "llm_gateway", "github", "subprocess", "apps.repositories.storage")))
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, {"eval", "exec", "compile"})
                    elif isinstance(node.func, ast.Attribute):
                        self.assertNotIn(node.func.attr, {"system", "Popen", "run"})
                    called = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                    if called == "import_module":
                        self.assertIsInstance(node.args[0], ast.Constant)
                        self.assertEqual(node.args[0].value, "agents.01_structure_analyzer.definition")

    def test_reader_adapter_uses_authorized_snapshot_services(self):
        from uuid import uuid4
        context = ExecutionContext.make(principal_id="user", workspace_id=uuid4(), repository_id=uuid4(),
            snapshot_id=uuid4(), commit_sha="a"*40, manifest_digest="b"*64, execution_id=uuid4(),
            agent_id="structure_analyzer", permissions={Permission.REPOSITORY_READ}, correlation_id="c", trace_id="t",
            cancellation=threading.Event())
        snapshot = SimpleNamespace(repository_id=context.repository_id, commit_sha=context.commit_sha,
                                   manifest_digest=context.manifest_digest)
        manifest = SimpleNamespace(files=(SimpleNamespace(path="src/app.py", size_bytes=3, sha256="c"*64),))
        reader = SnapshotRepositoryReader(user=object(), manifest_codec=object(), storage=object())
        with patch("apps.repositories.services.get_authorized_snapshot", return_value=snapshot) as authorize, \
             patch("apps.repositories.snapshot_service.read_snapshot_manifest", return_value=manifest):
            rows = reader.list_file_metadata(context)
        authorize.assert_called_once()
        self.assertEqual(rows, ({"path":"src/app.py", "size_bytes":3, "sha256":"c"*64},))

    def test_reader_adapter_rejects_wrong_manifest_and_unsealed_snapshot(self):
        context = StructureAnalysisTests().make_context()
        reader = SnapshotRepositoryReader(user=object(), manifest_codec=object(), storage=object())
        wrong = SimpleNamespace(repository_id=context.repository_id, commit_sha=context.commit_sha, manifest_digest="c"*64)
        with patch("apps.repositories.services.get_authorized_snapshot", return_value=wrong):
            with self.assertRaises(SnapshotUnavailableError): reader.list_file_metadata(context)
        from apps.repositories.errors import SnapshotNotSealed
        with patch("apps.repositories.services.get_authorized_snapshot", side_effect=SnapshotNotSealed("unsealed")):
            with self.assertRaises(SnapshotNotSealed): reader.list_file_metadata(context)

    def test_authorization_denial_fails_execution_closed(self):
        from apps.repositories.errors import WorkspaceAccessDenied
        class DeniedReader:
            def list_file_metadata(self, context): raise WorkspaceAccessDenied("denied")
        helper = StructureAnalysisTests()
        context = helper.make_context()
        registry = AgentRegistry()
        register_structure_analyzer(registry, DeniedReader())
        result = ExecutionLifecycle(registry).execute(helper.request(context), context)
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(result.errors[0].code, "agent_foundation_error")
