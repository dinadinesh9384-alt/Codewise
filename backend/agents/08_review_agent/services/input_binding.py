"""Schema-validated, bounded binding from completed workflow outputs to Agent 8."""

from importlib import import_module
from uuid import uuid5

from orchestration.contracts import WorkflowRequest


_OUTPUT_MODELS = {
    "structure_analyzer": ("agents.01_structure_analyzer.schemas.output", "StructureAnalysisOutput"),
    "business_logic_analyst": ("agents.02_business_logic_analyst.schemas.output", "BusinessAnalysisOutput"),
    "code_quality_analyst": ("agents.03_code_quality_analyst.schemas.output", "CodeQualityOutput"),
    "security_analyst": ("agents.04_security_analyst.schemas.output", "SecurityAnalysisOutput"),
    "migration_planner": ("agents.05_migration_planner.schemas.output", "MigrationPlanOutput"),
    "patch_author": ("agents.06_patch_author.schemas.output", "PatchAuthorOutput"),
    "test_engineer": ("agents.07_test_engineer.schemas.output", "TestEngineerOutput"),
}
_UPSTREAM = tuple(_OUTPUT_MODELS)


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _text(value, limit=1500):
    return str(value or "")[:limit]


def bind_review_input(workflow_request: WorkflowRequest, prior_results, lifecycle_statuses):
    schemas = import_module("agents.08_review_agent.schemas.input")
    statuses, outputs, evidence, findings, facts, tasks, patches, scenarios = [], {}, {}, [], [], [], [], []
    uncertainties, limitations, truncated, repository_context = [], [], False, None

    def add_fact(agent, kind, title, details, *, item_id=None, related=(), evidence_ids=(), status=None):
        nonlocal truncated
        if (len(str(title or "")) > 240 or len(str(details or "")) > 1500 or len(related) > 50 or
                len(evidence_ids) > 20 or len(str(status or "")) > 40):
            truncated = True
        if len(facts) >= 700:
            truncated = True
            return
        facts.append(schemas.ReviewFact(agent_id=agent, item_id=item_id, kind=kind,
            title=_text(title, 240) or kind, details=_text(details) or "No description was supplied upstream.", related_ids=tuple(related)[:50],
            evidence_ids=tuple(evidence_ids)[:20], status=_text(status, 40) or None))

    for agent_id in _UPSTREAM:
        selected = workflow_request.selected(agent_id)
        raw = (prior_results or {}).get(agent_id) if selected else None
        lifecycle = (lifecycle_statuses or {}).get(agent_id)
        if not selected:
            lifecycle = "skipped"
            raw = None
        elif lifecycle is None:
            lifecycle = "completed" if raw else "unavailable"
        if lifecycle not in {"completed", "skipped", "failed", "timed_out", "cancelled", "unavailable"}:
            lifecycle = "unavailable"
        if lifecycle != "completed":
            raw = None
        parsed = None
        validation_status = None
        analysis_status = None
        llm_status = None
        if raw is not None:
            try:
                module_path, class_name = _OUTPUT_MODELS[agent_id]
                output_model = getattr(import_module(module_path), class_name)
                parsed = output_model.model_validate(raw)
                if (parsed.execution_id != uuid5(workflow_request.execution_id, agent_id) or
                    (getattr(parsed, "agent_id", agent_id) != agent_id) or any(getattr(parsed, field) != expected for field, expected in (
                    ("repository_id", workflow_request.repository_id), ("snapshot_id", workflow_request.snapshot_id),
                    ("commit_sha", workflow_request.commit_sha), ("manifest_digest", workflow_request.manifest_digest)))):
                    parsed = None
                    validation_status = "rejected"
                    analysis_status = "snapshot_identity_mismatch"
                else:
                    validation_status = "passed"
            except Exception:
                validation_status = "rejected"
                analysis_status = "output_schema_invalid"
        if lifecycle == "completed" and parsed is None and validation_status is None:
            analysis_status = "missing_output"
        if parsed is not None:
            outputs[agent_id] = parsed
            llm_status = getattr(parsed, "llm_status", None)
            analysis_status = ("partial" if getattr(parsed, "coverage", None) == "partial" else
                getattr(parsed, "status", None) or getattr(parsed, "plan_status", None) or getattr(parsed, "coverage", None))
            if agent_id == "structure_analyzer":
                analysis_status = parsed.quality.completion_status
            for ref in getattr(parsed, "evidence", ()):
                serialized = ref.model_dump(mode="json", exclude={"metadata"})
                previous = evidence.get(ref.evidence_id)
                if previous is not None and previous != serialized:
                    validation_status = "rejected"
                    parsed = None
                    outputs.pop(agent_id, None)
                    break
                if len(evidence) < 5000:
                    evidence[ref.evidence_id] = serialized
                else:
                    truncated = True
                    break
            if parsed is not None:
                for ref in getattr(parsed, "evidence", ()):
                    if ref.snapshot_id != workflow_request.snapshot_id or ref.manifest_digest != workflow_request.manifest_digest:
                        validation_status = "rejected"
                        parsed = None
                        outputs.pop(agent_id, None)
                        break
        statuses.append(schemas.UpstreamReviewStatus(agent_id=agent_id, status=lifecycle, selected=selected,
            output_present=parsed is not None, llm_status=llm_status, analysis_status=_text(analysis_status, 40) or None,
            validation_status=validation_status))
        if parsed is None:
            continue
        uncertainties.extend(_text(item, 1000) for item in getattr(parsed, "uncertainties", ())[:30])
        limitations.extend(_text(item, 1000) for item in getattr(parsed, "limitations", ())[:30])
        for finding in getattr(parsed, "findings", ()):
            row = _plain(finding)
            refs = tuple(ref.evidence_id for ref in finding.evidence)
            if any(existing.finding_id == finding.finding_id for existing in findings):
                continue
            if len(findings) < 500:
                findings.append(schemas.ReviewFinding(agent_id=agent_id, finding_id=finding.finding_id,
                    category=str(getattr(finding.category, "value", finding.category)), title=_text(finding.title, 240),
                    description=_text(finding.description, 2000), severity=str(getattr(finding.severity, "value", finding.severity)),
                    confidence=finding.confidence, evidence_ids=refs, related_files=tuple(finding.related_files)[:30],
                    classification=row.get("classification"), remediation=row.get("remediation")))
            else:
                truncated = True
        if agent_id == "structure_analyzer":
            overview = parsed.overview
            if (len(overview.languages) > 30 or len(parsed.technologies) > 60 or len(parsed.entry_points) > 60 or
                    len(parsed.configurations) > 100 or sum(len(row.dependencies) for row in parsed.dependency_manifests) > 200):
                truncated = True
            limitations.extend(_text(item, 1000) for item in parsed.quality.limitations[:30])
            limitations.extend(_text(item, 1000) for item in parsed.quality.warnings[:30])
            repository_context = schemas.RepositoryReviewContext(file_count=overview.file_count, directory_count=overview.directory_count,
                files_inspected=parsed.quality.files_inspected, files_skipped=parsed.quality.files_skipped,
                completion_status=parsed.quality.completion_status,
                languages=tuple(x.language for x in overview.languages)[:30],
                technologies=tuple(x.name for x in parsed.technologies)[:60],
                entry_points=tuple(x.path for x in parsed.entry_points)[:60],
                configurations=tuple(x.path for x in parsed.configurations)[:100],
                dependencies=tuple(f"{manifest.ecosystem}:{dep.name}{' '+dep.constraint if dep.constraint else ''}"
                    for manifest in parsed.dependency_manifests for dep in manifest.dependencies)[:200],
                limitations=tuple(parsed.quality.limitations)[:30])
            for row in parsed.technologies:
                add_fact(agent_id, "technology", row.name, row.explanation,
                         evidence_ids=tuple(ref.evidence_id for ref in row.evidence))
            for row in parsed.observations:
                add_fact(agent_id, row.category, row.category, row.observation,
                         evidence_ids=tuple(ref.evidence_id for ref in row.evidence))
            for row in parsed.entry_points:
                add_fact(agent_id, "entry_point", row.path, row.explanation,
                         evidence_ids=tuple(ref.evidence_id for ref in row.evidence), status=row.classification)
            for row in parsed.configurations:
                add_fact(agent_id, "configuration", row.path, row.content_inspection,
                         evidence_ids=(row.evidence.evidence_id,), status=row.configuration_type)
            for manifest in parsed.dependency_manifests:
                for dependency in manifest.dependencies:
                    add_fact(agent_id, "repository_dependency", dependency.name,
                        f"{manifest.ecosystem} {dependency.constraint or ''}; group={dependency.group}; direct={dependency.direct}",
                        evidence_ids=((dependency.evidence.evidence_id,) if dependency.evidence else
                                      (manifest.evidence.evidence_id,)))
            add_fact(agent_id, "entry_point_summary", "Entry point summary", overview.entry_point_summary)
        if agent_id == "business_logic_analyst":
            for row in parsed.workflows:
                add_fact(agent_id, "workflow", row.name, row.description, item_id=row.workflow_id, evidence_ids=row.evidence_ids)
            for kind, rows in (("business_rule", parsed.business_rules), ("entity", parsed.entities),
                               ("relationship", parsed.relationships), ("execution_path", parsed.execution_paths)):
                for row in rows:
                    d = _plain(row)
                    details = d.get("description", d.get("relationship", d.get("completeness", "")))
                    if kind == "execution_path" and d.get("symbols"):
                        details = f"{details}; symbols: {', '.join(d['symbols'])}"
                    add_fact(agent_id, kind, d.get("title", d.get("operation", d.get("source_entity", kind))),
                        details,
                        item_id=d.get("claim_id", d.get("relationship_id", d.get("path_id"))),
                        evidence_ids=d.get("evidence_ids", ()))
            add_fact(agent_id, "business_overview", "Business overview", parsed.executive_overview)
            add_fact(agent_id, "domain_summary", "Domain summary", parsed.domain_summary)
        elif agent_id == "code_quality_analyst":
            for row in parsed.complexity_metrics:
                add_fact(agent_id, "quality_metric", row.path,
                         f"{row.lines_of_code} lines; functions={row.function_count}; classes={row.class_count}; "
                         f"average complexity={row.average_cyclomatic_complexity}; maximum complexity={row.maximum_cyclomatic_complexity}; "
                         f"nesting depth={row.maximum_nesting_depth}; missing annotations={row.missing_public_annotations}.",
                         evidence_ids=tuple(ref.evidence_id for ref in row.evidence))
            for row in parsed.function_metrics:
                add_fact(agent_id, "function_metric", row.qualified_name,
                         f"path={row.path}; lines {row.start_line}-{row.end_line}; length={row.length}; "
                         f"complexity={row.cyclomatic_complexity}; nesting={row.nesting_depth}; "
                         f"parameters={row.parameter_count}; missing annotations={row.missing_annotations}.",
                         evidence_ids=tuple(ref.evidence_id for ref in row.evidence))
            for row in parsed.recommendations:
                add_fact(agent_id, "recommendation", row.recommendation_id, row.rationale,
                         item_id=row.recommendation_id, related=row.finding_ids, evidence_ids=row.evidence_ids)
            add_fact(agent_id, "quality_overview", "Quality overview", parsed.overview, status=parsed.coverage)
        elif agent_id == "security_analyst":
            for row in parsed.assessments:
                add_fact(agent_id, "security_assessment", row.finding_id, row.rationale,
                         item_id=row.finding_id, related=(row.finding_id,), status=row.assessment)
            for row in (*parsed.insights, *parsed.recommendations):
                d = _plain(row)
                add_fact(agent_id, "security_recommendation" if hasattr(row, "action") else "security_insight",
                    d.get("action", d.get("title", "Security context")), d.get("rationale", d.get("explanation", "")),
                    item_id=d.get("recommendation_id", d.get("insight_id")), related=d.get("finding_ids", ()),
                    evidence_ids=d.get("evidence_ids", ()))
            add_fact(agent_id, "security_overview", "Security overview", parsed.overview, status=parsed.coverage)
        elif agent_id == "migration_planner":
            for row in parsed.tasks:
                if len(tasks) < 500:
                    tasks.append(schemas.ReviewTask(task_id=row.task_id, title=row.title, rationale=row.rationale,
                        priority=row.priority, risk=row.risk, depends_on=row.depends_on,
                        finding_ids=row.upstream_finding_ids, evidence_ids=row.evidence_ids,
                        expected_outcome=row.expected_outcome, affected_files=row.affected_files))
                else:
                    truncated = True
            for row in parsed.risks:
                add_fact(agent_id, "migration_risk", row.title, row.description, item_id=row.risk_id,
                         related=row.upstream_finding_ids, evidence_ids=row.evidence_ids, status=row.severity)
            for row in parsed.declared_dependencies:
                add_fact(agent_id, "declared_dependency", row.name, f"{row.ecosystem} {row.version_spec or ''} {row.exact_version or ''}",
                         evidence_ids=row.evidence_ids)
            for row in parsed.internal_dependencies:
                add_fact(agent_id, "internal_dependency", row.source_module, f"depends on {row.target_module}",
                         evidence_ids=(row.evidence_id,))
            for row in parsed.phases:
                add_fact(agent_id, "migration_phase", row.title, row.purpose, item_id=row.phase_id,
                         related=(*row.depends_on_phase_ids, *row.task_ids), status=str(row.order))
            for row in parsed.inventory_facts:
                add_fact(agent_id, "migration_inventory", row.name, row.value, item_id=row.fact_id,
                         evidence_ids=row.evidence_ids, status=row.category)
            if parsed.database_consideration:
                row = parsed.database_consideration
                add_fact(agent_id, "database_constraint", "Database migration consideration", row.assessment,
                         evidence_ids=row.evidence_ids, status=f"{row.status}:{row.data_migration_required}")
            if parsed.api_consideration:
                row = parsed.api_consideration
                add_fact(agent_id, "api_constraint", "API compatibility consideration", row.assessment,
                         evidence_ids=row.evidence_ids, status=row.status)
                for concern in row.breaking_change_risks:
                    add_fact(agent_id, "api_breaking_change_risk", "Breaking change risk", concern,
                             evidence_ids=row.evidence_ids)
            add_fact(agent_id, "migration_plan", "Migration plan", parsed.current_state_assessment,
                     status=parsed.plan_status)
        elif agent_id == "patch_author":
            for row in parsed.source_findings:
                add_fact(agent_id, "patch_source_finding", row.title, row.description, item_id=row.finding_id,
                         related=(row.finding_id,), evidence_ids=tuple(ref.evidence_id for ref in row.evidence))
            for row in parsed.proposals:
                patches.append(schemas.ReviewPatch(proposal_id=row.proposal_id, path=row.path,
                    validation_status=parsed.validation_status, proposal_status=parsed.status,
                    original_content_hash=row.original_content_hash, proposed_content_hash=row.proposed_content_hash,
                    diff=row.unified_diff, source_finding_ids=row.related_finding_ids,
                    migration_task_ids=row.migration_task_ids, evidence_ids=row.evidence_ids,
                    summary=row.expected_behavior, rationale=row.rationale, required_tests=row.required_tests,
                    risk=row.risk, independently_validated=False))
            add_fact(agent_id, "patch_summary", "Patch summary", parsed.summary, status=parsed.validation_status)
            uncertainties.extend(_text(row, 1000) for row in parsed.uncertainties[:30])
        elif agent_id == "test_engineer":
            for row in parsed.scenarios:
                scenarios.append(schemas.ReviewScenario(scenario_id=row.scenario_id, title=row.title,
                    scenario_type=row.scenario_type, test_level=row.test_level, rationale=row.rationale,
                    preconditions=row.preconditions, inputs=row.inputs, steps=row.steps,
                    priority=row.priority, related_finding_ids=row.related_finding_ids,
                    migration_task_ids=row.migration_task_ids, patch_proposal_ids=row.patch_proposal_ids,
                    evidence_ids=row.evidence_ids, execution_status=row.execution_status,
                    expected_results=row.expected_results, framework=row.framework,
                    claim_validation=row.claim_validation))
            for row in parsed.test_files:
                add_fact(agent_id, "test_inventory", row.path,
                    f"{row.test_case_count} test cases; {row.fixture_count} fixtures; analysis {row.analysis_status}",
                    evidence_ids=row.evidence_ids)
            for row in parsed.coverage_observations:
                add_fact(agent_id, "coverage_report", row.report_path,
                    f"Snapshot report records {row.percent_covered}% coverage; provenance={row.provenance}; independently_verified={row.independently_verified}.",
                    evidence_ids=row.evidence_ids)
            add_fact(agent_id, "test_plan", "Test plan", parsed.summary, status=parsed.status)
    evidence_models = tuple(import_module("agent_foundation.contracts.evidence").EvidenceReference.model_validate(row)
                            for row in evidence.values())
    known_item_ids = ({row.finding_id for row in findings} | {row.task_id for row in tasks} |
        {row.proposal_id for row in patches} | {row.scenario_id for row in scenarios} |
        {row.item_id for row in facts if row.item_id})
    bounded_facts = []
    for fact in facts:
        linked = tuple(item for item in fact.related_ids if item in known_item_ids)
        if len(linked) != len(fact.related_ids):
            truncated = True
        bounded_facts.append(fact.model_copy(update={"related_ids": linked}))
    # All stable references are validated here; input model validation repeats these checks at lifecycle entry.
    return schemas.ReviewAgentInput(execution_id=uuid5(workflow_request.execution_id, "review_agent"),
        workspace_id=workflow_request.workspace_id, repository_id=workflow_request.repository_id,
        snapshot_id=workflow_request.snapshot_id, commit_sha=workflow_request.commit_sha,
        manifest_digest=workflow_request.manifest_digest, upstream_statuses=tuple(statuses),
        repository=repository_context, findings=tuple(findings), facts=tuple(bounded_facts),
        migration_tasks=tuple(tasks), patch_proposals=tuple(patches), test_scenarios=tuple(scenarios),
        evidence=evidence_models, uncertainties=tuple(uncertainties[:100]), limitations=tuple(limitations[:100]),
        upstream_truncated=truncated)
