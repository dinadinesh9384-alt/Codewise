import hashlib
import threading
import uuid

from django.test import SimpleTestCase

from agent_foundation.context import ExecutionContext
from agent_foundation.contracts.execution import ExecutionRequest, ExecutionStatus
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from agents.registry import register_structure_analyzer


class MemoryReader:
    def __init__(self, files): self.files, self.reads = files, []
    def list_file_metadata(self, context):
        return tuple({"path":p, "size_bytes":len(data), "sha256":hashlib.sha256(data).hexdigest()} for p,data in sorted(self.files.items()))
    def list_paths(self, context): return tuple(self.files)
    def file_metadata(self, context, path): return next(item for item in self.list_file_metadata(context) if item["path"] == path)
    def read_file(self, context, path, *, max_bytes):
        self.reads.append(path)
        data = self.files[path]
        if len(data) > max_bytes: raise ValueError("bounded read rejected")
        return data


class StructureAnalysisTests(SimpleTestCase):
    def make_context(self):
        return ExecutionContext.make(principal_id="user", workspace_id=uuid.uuid4(), repository_id=uuid.uuid4(),
            snapshot_id=uuid.uuid4(), commit_sha="a"*40, manifest_digest="b"*64, execution_id=uuid.uuid4(),
            agent_id="structure_analyzer", permissions={Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ},
            correlation_id="c", trace_id="t", cancellation=threading.Event())

    def request(self, context, **options):
        return ExecutionRequest(execution_id=context.execution_id, agent_id="structure_analyzer", agent_version="1.0.0",
            input={"execution_id":str(context.execution_id), "workspace_id":str(context.workspace_id),
                "repository_id":str(context.repository_id), "snapshot_id":str(context.snapshot_id),
                "commit_sha":context.commit_sha, "manifest_digest":context.manifest_digest, "options":options},
            repository_id=context.repository_id, snapshot_id=context.snapshot_id, workspace_id=context.workspace_id,
            commit_sha=context.commit_sha, manifest_digest=context.manifest_digest,
            correlation_id=context.correlation_id, trace_id=context.trace_id)

    def test_lifecycle_execution_is_deterministic_and_snapshot_bound(self):
        files = {"pyproject.toml":b"[project]\ndependencies=['django>=5']\n", "manage.py":b"print('do not execute')\n",
            "src/app.py":b"__import__('pathlib').Path('owned').write_text('x')\n", ".env":b"TOKEN=do-not-return\n", "tests/test_app.py":b"assert True\n"}
        reader = MemoryReader(files)
        registry = AgentRegistry()
        definition = register_structure_analyzer(registry, reader)
        self.assertFalse(definition.contract.requires_llm)
        self.assertEqual(definition.contract.agent_id, "structure_analyzer")
        ctx, lifecycle = self.make_context(), ExecutionLifecycle(registry)
        first = lifecycle.execute(self.request(ctx), ctx)
        second = lifecycle.execute(self.request(ctx), ctx)
        self.assertEqual(first.status, ExecutionStatus.COMPLETED, first.errors)
        self.assertEqual(first.output.model_dump(), second.output.model_dump())
        self.assertIn("Django", [item.name for item in first.output.technologies])
        django = next(item for manifest in first.output.dependency_manifests for item in manifest.dependencies if item.name.casefold() == "django")
        self.assertEqual(django.evidence.start_line, 2)
        self.assertTrue(any(item.sensitive for item in first.output.configurations))
        self.assertNotIn("do-not-return", first.output.model_dump_json())
        self.assertTrue(all(ref.snapshot_id == ctx.snapshot_id and ref.manifest_digest == ctx.manifest_digest for ref in first.output.evidence))
        self.assertEqual(reader.reads, ["pyproject.toml", "pyproject.toml"])

    def test_limits_are_reported_and_empty_repository_is_valid(self):
        registry = AgentRegistry()
        register_structure_analyzer(registry, MemoryReader({}))
        ctx, lifecycle = self.make_context(), ExecutionLifecycle(registry)
        empty = lifecycle.execute(self.request(ctx), ctx)
        self.assertEqual(empty.status, ExecutionStatus.COMPLETED)
        self.assertEqual(empty.output.overview.file_count, 0)
        registry = AgentRegistry()
        register_structure_analyzer(registry, MemoryReader({"a":b"#!/bin/sh\necho a\n", "b":b"#!/bin/sh\necho b\n"}))
        ctx2 = self.make_context()
        limited = ExecutionLifecycle(registry).execute(self.request(ctx2, max_files=1), ctx2)
        self.assertEqual(limited.status, ExecutionStatus.COMPLETED)
        self.assertEqual(limited.output.quality.files_skipped, 1)
        self.assertIn("maximum_files", {item.reason for item in limited.output.quality.skipped_files})

    def test_dependency_switch_prevents_manifest_content_reads(self):
        reader = MemoryReader({"pyproject.toml":b"[project]\ndependencies=['django']\n"})
        registry = AgentRegistry()
        register_structure_analyzer(registry, reader)
        context = self.make_context()
        result = ExecutionLifecycle(registry).execute(self.request(context, inspect_dependencies=False), context)
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(reader.reads, [])
        self.assertEqual(result.output.dependency_manifests, ())

    def test_file_byte_and_directory_depth_limits_are_reported(self):
        cases = [
            ({"a":b"12345"}, {"max_file_size_bytes":2}, "maximum_file_size"),
            ({"a":b"12345"}, {"max_total_content_bytes":2}, "maximum_total_content_bytes"),
            ({"nested/a":b"1"}, {"max_directory_depth":0}, "maximum_directory_depth"),
        ]
        for files, options, reason in cases:
            registry = AgentRegistry()
            register_structure_analyzer(registry, MemoryReader(files))
            context = self.make_context()
            result = ExecutionLifecycle(registry).execute(self.request(context, **options), context)
            self.assertEqual(result.status, ExecutionStatus.COMPLETED, result.errors)
            self.assertIn(reason, {item.reason for item in result.output.quality.skipped_files})
