"""Bounded deterministic analysis service over an authorized snapshot reader."""

import hashlib
from collections import defaultdict

from agent_foundation.contracts.evidence import EvidenceReference, EvidenceType
from agent_foundation.errors import EvidenceValidationError, SnapshotUnavailableError
from agent_foundation.validation import validate_evidence

from ..components.configuration_detector import detect_configurations, is_sensitive_path
from ..components.dependency_detector import detect_dependency_manifests
from ..components.entry_point_detector import detect_entry_points
from ..components.file_classifier import EXTENSIONS, classify_file
from ..components.framework_detector import detect_frameworks
from ..components.language_detector import detect_language
from ..components.statistics import calculate_statistics
from ..components.structure_analyzer import build_findings, observe_structure
from ..schemas.input import StructureAnalysisInput
from ..schemas.output import StructureAnalysisOutput
from ..schemas.repository import AnalysisQuality, RepositoryOverview, SkippedFile
from ..tools.repository_inspector import RepositoryInspector

AGENT_ID = "structure_analyzer"
AGENT_VERSION = "1.0.0"
_DEPENDENCY_FILES = {"requirements.txt", "pyproject.toml", "pipfile", "package.json", "pom.xml",
                     "build.gradle", "build.gradle.kts", "cargo.toml", "go.mod", "composer.json", "gemfile"}
_ENTRY_NAMES = {"manage.py", "wsgi.py", "asgi.py", "app.py", "main.py", "server.py", "index.js", "index.ts",
                "main.ts", "program.cs", "main.java", "main.go", "main.rs"}


def _content_is_relevant(path, options):
    name = path.rsplit("/", 1)[-1].casefold()
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if (name in _DEPENDENCY_FILES or name.endswith(".csproj")) and options.inspect_dependencies:
        return True
    if name == "package.json" and options.identify_entry_points:
        return True
    if suffix:
        return False
    return name not in {"dockerfile", "makefile", "jenkinsfile", "justfile", "license", "notice"}


def analyze_repository(request: StructureAnalysisInput, context, reader) -> StructureAnalysisOutput:
    if (request.execution_id != context.execution_id or request.workspace_id != context.workspace_id
            or request.repository_id != context.repository_id or request.snapshot_id != context.snapshot_id
            or request.commit_sha != context.commit_sha or request.manifest_digest != context.manifest_digest):
        raise SnapshotUnavailableError("Analysis input is not bound to the execution snapshot.")
    inspector = RepositoryInspector(reader, context)
    records = inspector.inventory()
    by_path = {item["path"]: item for item in records}
    options = request.options
    skip_reason: dict[str, str] = {}
    inspection_candidates = [record for record in records if _content_is_relevant(record["path"], options)]
    inspection_candidates.sort(key=lambda record: (0 if options.inspect_dependencies and
        (record["path"].rsplit("/", 1)[-1].casefold() in _DEPENDENCY_FILES
         or record["path"].rsplit("/", 1)[-1].casefold().endswith(".csproj")) else 1, record["path"]))
    selected = set(record["path"] for record in inspection_candidates[:options.max_files])

    # Prioritize useful manifests and conventional entry points within the shared byte budget.
    candidates = []
    for record in records:
        path = record["path"]
        name = path.rsplit("/", 1)[-1].casefold()
        depth = path.count("/")
        relevant = _content_is_relevant(path, options)
        if depth > options.max_directory_depth:
            if relevant or is_sensitive_path(path): skip_reason[path] = "maximum_directory_depth"
        elif is_sensitive_path(path):
            skip_reason[path] = "sensitive_content_intentionally_skipped"
        elif not relevant:
            continue
        elif path not in selected:
            skip_reason[path] = "maximum_files"
        elif record["size_bytes"] > options.max_file_size_bytes:
            skip_reason[path] = "maximum_file_size"
        else:
            priority = 0 if name in _DEPENDENCY_FILES else (1 if name in _ENTRY_NAMES else 2)
            candidates.append((priority, record["size_bytes"], path, record))
    candidates.sort(key=lambda item: item[:3])

    contents: dict[str, bytes] = {}
    content_bytes = 0
    for _priority, _size, path, record in candidates:
        context.check_active()
        size = record["size_bytes"]
        if size > options.max_total_content_bytes - content_bytes:
            skip_reason[path] = "maximum_total_content_bytes"
            continue
        data = b"" if size == 0 else inspector.read(path, max_bytes=min(size, options.max_file_size_bytes,
            options.max_total_content_bytes - content_bytes))
        if not isinstance(data, bytes) or len(data) > size or len(data) > options.max_file_size_bytes:
            raise SnapshotUnavailableError("Snapshot reader returned content outside its declared bounds.")
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise SnapshotUnavailableError("Snapshot reader returned content inconsistent with its manifest.")
        contents[path] = data
        try:
            record["line_count"] = len(data.decode("utf-8").splitlines())
        except UnicodeDecodeError:
            pass
        content_bytes += len(data)

    evidence_cache = {}
    def evidence_for(path, source_tool_id, start_line=None, end_line=None):
        cache_key = (path, start_line, end_line)
        if cache_key in evidence_cache:
            existing = evidence_cache[cache_key]
            return existing.model_copy(update={"source_tool_id": source_tool_id})
        record = by_path.get(path)
        if record is None:
            raise EvidenceValidationError("Detector requested evidence outside the sealed manifest.")
        stable_id = hashlib.sha256(f"{request.snapshot_id}:{path}:{record['sha256']}:{start_line}:{end_line}".encode()).hexdigest()[:32]
        reference = EvidenceReference(evidence_id=stable_id, evidence_type=EvidenceType.FILE,
            snapshot_id=request.snapshot_id, manifest_digest=request.manifest_digest, path=path,
            start_line=start_line, end_line=end_line, content_hash=record["sha256"],
            source_tool_id=source_tool_id, direct=True)
        evidence_cache[cache_key] = reference
        return reference

    configs = detect_configurations(records, evidence_for)
    dependencies = detect_dependency_manifests(records, contents, evidence_for) if options.inspect_dependencies else ()
    technologies = detect_frameworks(by_path, dependencies, evidence_for, include_frameworks=options.detect_frameworks)
    entries = detect_entry_points(by_path, contents, evidence_for, enabled=options.identify_entry_points)

    languages = {}
    for record in records:
        context.check_active()
        path = record["path"]
        languages[path] = detect_language(path, contents.get(path))
    tree, directory_count, max_depth, language_stats, distributions, depth_skips, size_distributions = calculate_statistics(
        records, languages, max_directory_depth=options.max_directory_depth, evidence_for=evidence_for)
    for path in depth_skips:
        skip_reason.setdefault(path, "maximum_directory_depth")
    largest = tuple({"path": item["path"], "size_bytes": item["size_bytes"]}
                    for item in sorted(records, key=lambda item: (-item["size_bytes"], item["path"]))[:10])
    observations = observe_structure(by_path, technologies, configs, entries, language_stats, evidence_for)
    findings = build_findings(technologies, entries, observations)

    evidence = tuple(evidence_cache[key] for key in sorted(evidence_cache, key=lambda item: (item[0], item[1] or 0, item[2] or 0)))
    for ref in evidence:
        validate_evidence(ref, snapshot_id=request.snapshot_id, manifest_digest=request.manifest_digest,
                          file_metadata=lambda path: by_path[path])
    inspected_count = len(contents)
    skipped = tuple(SkippedFile(path=path, reason=reason, size_bytes=by_path[path]["size_bytes"])
                    for path, reason in sorted(skip_reason.items()))
    malformed = tuple(item.manifest_path for item in dependencies if item.parse_status == "malformed")
    dependency_skipped = tuple(item.manifest_path for item in dependencies if item.parse_status == "skipped_by_content_limit")
    limitations = ["Empty directories cannot be represented by the current file-only repository manifest."]
    if any(reason in {"maximum_files", "maximum_file_size", "maximum_total_content_bytes", "maximum_directory_depth"}
           for reason in skip_reason.values()):
        limitations.append("Some repository content was not inspected because configured analysis limits were reached.")
    if any(item.parse_status == "malformed" for item in dependencies):
        limitations.append("One or more dependency manifests could not be parsed safely.")
    if any(item.parse_status == "skipped_by_content_limit" for item in dependencies):
        limitations.append("Some dependency declarations were unavailable because the content limits were reached.")
    quality = AnalysisQuality(completion_status="partial" if skip_reason or malformed or dependency_skipped else "complete",
        files_inspected=inspected_count, files_skipped=len(skip_reason), content_bytes_inspected=content_bytes, skipped_files=skipped,
        limitations=tuple(limitations), warnings=tuple(
            [f"Dependency declarations could not be parsed safely: {path}" for path in malformed] +
            [f"Dependency declarations were skipped by content limits: {path}" for path in dependency_skipped]))
    overview = RepositoryOverview(file_count=len(records), directory_count=directory_count, total_bytes=sum(i["size_bytes"] for i in records),
        maximum_directory_depth=max_depth, largest_files=largest, file_distribution=distributions,
        size_distribution=size_distributions, languages=language_stats,
        technologies=technologies, entry_point_summary=(f"{len(entries)} entry-point candidates found." if entries else "No entry-point candidates identified."))
    return StructureAnalysisOutput(agent_version=AGENT_VERSION, execution_id=request.execution_id,
        repository_id=request.repository_id, snapshot_id=request.snapshot_id, commit_sha=request.commit_sha,
        manifest_digest=request.manifest_digest, options=options, overview=overview, tree=tree, technologies=technologies,
        configurations=configs, dependency_manifests=dependencies, entry_points=entries,
        observations=observations, quality=quality, findings=findings, evidence=evidence)
