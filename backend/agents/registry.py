"""Explicit trusted agent registration entry points; no package scanning is used."""

from importlib import import_module

from agent_foundation.registry import AgentRegistry
from orchestration.graph import AgentNodeBinding
from orchestration.contracts import WorkflowRequest
from uuid import uuid5


def register_available_agents(registry: AgentRegistry, repository_reader):
    """Register only the official implementations present in this deployment."""
    return (register_structure_analyzer(registry, repository_reader),
            register_business_logic_analyst(registry, repository_reader),
            register_code_quality_analyst(registry, repository_reader),
            register_security_analyst(registry, repository_reader),
            register_migration_planner(registry, repository_reader),
            register_patch_author(registry, repository_reader),
            register_test_engineer(registry, repository_reader),
            register_review_agent(registry, repository_reader))


def register_structure_analyzer(registry: AgentRegistry, repository_reader):
    # This fixed module is maintained by CodeWise. No repository-controlled import path is accepted.
    module = import_module("agents.01_structure_analyzer.definition")
    return module.register(registry, repository_reader)


def register_business_logic_analyst(registry: AgentRegistry, repository_reader):
    # Fixed trusted specialist module; repository-controlled import paths are never used.
    module = import_module("agents.02_business_logic_analyst.definition")
    return module.register(registry, repository_reader)


def register_code_quality_analyst(registry: AgentRegistry, repository_reader):
    # Fixed trusted specialist module; repository-controlled import paths are never used.
    module = import_module("agents.03_code_quality_analyst.definition")
    return module.register(registry, repository_reader)


def register_security_analyst(registry: AgentRegistry, repository_reader):
    # Fixed trusted specialist module; repository-controlled import paths are never accepted.
    module = import_module("agents.04_security_analyst.definition")
    return module.register(registry, repository_reader)


def register_migration_planner(registry: AgentRegistry, repository_reader):
    module = import_module("agents.05_migration_planner.definition")
    return module.register(registry, repository_reader)


def register_patch_author(registry: AgentRegistry, repository_reader):
    module = import_module("agents.06_patch_author.definition")
    return module.register(registry, repository_reader)


def register_test_engineer(registry: AgentRegistry, repository_reader):
    module = import_module("agents.07_test_engineer.definition")
    return module.register(registry, repository_reader)


def register_review_agent(registry: AgentRegistry, repository_reader):
    module = import_module("agents.08_review_agent.definition")
    return module.register(registry, repository_reader)


def structure_analyzer_binding(context_factory, *, options=None):
    """Bind deterministic Agent 1 to the shared lifecycle and request snapshot."""
    def make_input(workflow_request: WorkflowRequest, prior_results):
        module = import_module("agents.01_structure_analyzer.schemas.input")
        return module.StructureAnalysisInput(execution_id=uuid5(workflow_request.execution_id, "structure_analyzer"),
            workspace_id=workflow_request.workspace_id, repository_id=workflow_request.repository_id,
            snapshot_id=workflow_request.snapshot_id, commit_sha=workflow_request.commit_sha,
            manifest_digest=workflow_request.manifest_digest, **({"options": options} if options else {}))
    return AgentNodeBinding(input_factory=make_input, context_factory=context_factory)


def business_logic_analyst_binding(context_factory, *, options=None):
    """Bind Agent 2 to the same snapshot after the mandatory structure result."""
    def make_input(workflow_request: WorkflowRequest, prior_results):
        module = import_module("agents.02_business_logic_analyst.schemas.input")
        return module.BusinessAnalysisInput(execution_id=uuid5(workflow_request.execution_id, "business_logic_analyst"),
            workspace_id=workflow_request.workspace_id, repository_id=workflow_request.repository_id,
            snapshot_id=workflow_request.snapshot_id, commit_sha=workflow_request.commit_sha,
            manifest_digest=workflow_request.manifest_digest, **({"options": options} if options else {}))
    return AgentNodeBinding(input_factory=make_input, context_factory=context_factory)


def code_quality_analyst_binding(context_factory, *, options=None):
    """Bind Agent 3 within the optional code quality, security, and planning group."""
    def make_input(workflow_request: WorkflowRequest, prior_results):
        module = import_module("agents.03_code_quality_analyst.schemas.input")
        return module.CodeQualityInput(execution_id=uuid5(workflow_request.execution_id, "code_quality_analyst"),
            workspace_id=workflow_request.workspace_id, repository_id=workflow_request.repository_id,
            snapshot_id=workflow_request.snapshot_id, commit_sha=workflow_request.commit_sha,
            manifest_digest=workflow_request.manifest_digest, **({"options": options} if options else {}))
    return AgentNodeBinding(input_factory=make_input, context_factory=context_factory)


def security_analyst_binding(context_factory, *, options=None):
    """Bind Agent 4 after the mandatory Agent 1 and Agent 2 stages."""
    def make_input(workflow_request: WorkflowRequest, prior_results):
        module = import_module("agents.04_security_analyst.schemas.input")
        return module.SecurityAnalysisInput(execution_id=uuid5(workflow_request.execution_id, "security_analyst"),
            workspace_id=workflow_request.workspace_id, repository_id=workflow_request.repository_id,
            snapshot_id=workflow_request.snapshot_id, commit_sha=workflow_request.commit_sha,
            manifest_digest=workflow_request.manifest_digest, **({"options": options} if options else {}))
    return AgentNodeBinding(input_factory=make_input, context_factory=context_factory)


def migration_planner_binding(context_factory, *, options=None):
    """Bind only completed and selected upstream findings to Agent 5."""
    def make_input(workflow_request: WorkflowRequest, prior_results):
        module = import_module("agents.05_migration_planner.schemas.input")
        agent_ids = ["structure_analyzer", "business_logic_analyst"]
        agent_ids.extend(agent_id for agent_id in ("code_quality_analyst", "security_analyst")
                         if workflow_request.selected(agent_id))
        findings, seen, truncated = [], set(), False
        for agent_id in agent_ids:
            output = (prior_results or {}).get(agent_id)
            if not output:
                continue
            for row in output.get("findings", ()):
                refs = row.get("evidence", ())
                if not refs:
                    continue
                finding_id = row.get("finding_id")
                if not isinstance(finding_id, str) or finding_id in seen:
                    continue
                evidence = []
                for ref in refs[:8]:
                    try:
                        evidence.append(module.EvidenceReference.model_validate(ref))
                    except Exception:
                        continue
                if not evidence:
                    continue
                if len(findings) >= 200:
                    truncated = True
                    break
                paths = {ref.path for ref in evidence if ref.path}
                findings.append(module.UpstreamFinding(agent_id=agent_id, finding_id=finding_id,
                    category=str(row.get("category", "observation"))[:80],
                    title=str(row.get("title", "Upstream finding"))[:240],
                    description=str(row.get("description", ""))[:1200],
                    severity=str(row.get("severity", "info")).lower(), evidence=tuple(evidence),
                    related_files=tuple(path for path in row.get("related_files", ()) if path in paths)[:30]))
                seen.add(finding_id)
            if truncated:
                break
        kwargs = {"execution_id": uuid5(workflow_request.execution_id, "migration_planner"),
            "workspace_id": workflow_request.workspace_id, "repository_id": workflow_request.repository_id,
            "snapshot_id": workflow_request.snapshot_id, "commit_sha": workflow_request.commit_sha,
            "manifest_digest": workflow_request.manifest_digest, "expected_upstream_agents": tuple(agent_ids),
            "upstream_findings": tuple(findings), "upstream_findings_truncated": truncated}
        if options:
            kwargs["options"] = options
        return module.MigrationAnalysisInput(**kwargs)
    return AgentNodeBinding(input_factory=make_input, context_factory=context_factory)


def patch_author_binding(context_factory, *, options=None):
    """Bind the completed, selected upstream results to the review-only patch author."""
    def make_input(workflow_request: WorkflowRequest, prior_results):
        module = import_module("agents.06_patch_author.schemas.input")
        upstream_ids = ("structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                        "security_analyst", "migration_planner")
        statuses, findings, tasks, evidence = [], [], [], {}
        dropped_context_agents = set()
        def store_evidence(parsed, agent_id):
            previous = evidence.get(parsed.evidence_id)
            if previous is not None:
                if previous != parsed:
                    dropped_context_agents.add(agent_id)
                    return False
                return True
            if len(evidence) >= 5000:
                dropped_context_agents.add(agent_id)
                return False
            evidence[parsed.evidence_id] = parsed
            return True
        for agent_id in upstream_ids:
            result = (prior_results or {}).get(agent_id)
            selected = workflow_request.selected(agent_id)
            status = "completed" if result else ("skipped" if not selected else "unavailable")
            statuses.append(module.UpstreamStatus(agent_id=agent_id, status=status))
            if not result:
                continue
            for ref in result.get("evidence", ()):
                try:
                    parsed = module.EvidenceReference.model_validate(ref)
                except Exception:
                    continue
                evidence[parsed.evidence_id] = parsed
            for row in result.get("findings", ()):
                refs = []
                for ref in row.get("evidence", ())[:8]:
                    try:
                        parsed = module.EvidenceReference.model_validate(ref)
                    except Exception:
                        continue
                    refs.append(parsed)
                    evidence[parsed.evidence_id] = parsed
                if not refs or len(findings) >= 300:
                    continue
                paths = {ref.path for ref in refs if ref.path}
                finding_id = row.get("finding_id")
                if not isinstance(finding_id, str) or any(item.finding_id == finding_id for item in findings):
                    continue
                category = {"structure_analyzer": "structure", "business_logic_analyst": "business_logic",
                    "code_quality_analyst": "quality", "security_analyst": "security"}[agent_id]
                findings.append(module.PatchFinding(agent_id=agent_id, finding_id=finding_id,
                    category=category, confidence=row.get("confidence", 0.5),
                    title=str(row.get("title", "Upstream finding"))[:240],
                    description=str(row.get("description", ""))[:1600],
                    severity=str(row.get("severity", "info")).lower(), evidence=tuple(refs),
                    related_files=tuple(path for path in row.get("related_files", ()) if path in paths)[:30]))
            if agent_id == "migration_planner":
                for row in result.get("tasks", ()):
                    refs = tuple(row.get("evidence_ids", ()))
                    if len(tasks) >= 500 or not refs or not set(refs) <= set(evidence):
                        continue
                    tasks.append(module.MigrationTaskReference(task_id=row["task_id"],
                        title=str(row["title"])[:240], rationale=str(row["rationale"])[:2000],
                        evidence_ids=refs, affected_files=tuple(row.get("affected_files", ()))[:50]))
        kwargs = {"execution_id": uuid5(workflow_request.execution_id, "patch_author"),
            "workspace_id": workflow_request.workspace_id, "repository_id": workflow_request.repository_id,
            "snapshot_id": workflow_request.snapshot_id, "commit_sha": workflow_request.commit_sha,
            "manifest_digest": workflow_request.manifest_digest, "upstream_statuses": tuple(statuses),
            "findings": tuple(findings), "migration_tasks": tuple(tasks),
            "evidence": tuple(evidence.values()), "upstream_truncated": len(findings) >= 300 or len(tasks) >= 500}
        if options:
            kwargs["options"] = options
        return module.PatchAuthorInput(**kwargs)
    return AgentNodeBinding(input_factory=make_input, context_factory=context_factory)


def test_engineer_binding(context_factory, *, options=None):
    """Bind Agent 7 to snapshot evidence and completed upstream results."""
    def make_input_with_state(workflow_request: WorkflowRequest, prior_results, lifecycle_statuses):
        module = import_module("agents.07_test_engineer.schemas.input")
        upstream_ids = ("structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                        "security_analyst", "migration_planner", "patch_author")
        statuses, findings, tasks, evidence = [], [], [], {}
        dropped_context_agents = set()
        def store_evidence(parsed, agent_id):
            previous = evidence.get(parsed.evidence_id)
            if previous is not None:
                if previous != parsed:
                    dropped_context_agents.add(agent_id)
                    return False
                return True
            if len(evidence) >= 5000:
                dropped_context_agents.add(agent_id)
                return False
            evidence[parsed.evidence_id] = parsed
            return True
        usable_outputs = {}
        category_agents = set(upstream_ids[:4])
        for agent_id in upstream_ids:
            result = (prior_results or {}).get(agent_id)
            selected = workflow_request.selected(agent_id)
            if not selected:
                result = None
            lifecycle = (lifecycle_statuses or {}).get(agent_id)
            if lifecycle is None:
                lifecycle = "completed" if result else ("skipped" if not selected else "unavailable")
            valid_identity = bool(result) and all(result.get(key) == expected for key, expected in (
                ("repository_id", str(workflow_request.repository_id)), ("snapshot_id", str(workflow_request.snapshot_id)),
                ("commit_sha", workflow_request.commit_sha), ("manifest_digest", workflow_request.manifest_digest)))
            if result and not valid_identity:
                result = None
            lifecycle_status = lifecycle if lifecycle in {"completed", "skipped", "failed", "timed_out", "cancelled"} else "unavailable"
            if lifecycle_status == "completed" and result is None:
                lifecycle_status = "unavailable"
            if lifecycle_status != "completed":
                result = None
            llm_status = result.get("llm_status") if result and result.get("llm_status") in {
                "completed", "unavailable", "failed", "not_attempted"} else None
            analysis_status = "snapshot_mismatch" if (prior_results or {}).get(agent_id) and not valid_identity else None
            if result:
                analysis_status = next((result[key] for key in ("status", "plan_status", "coverage")
                                        if isinstance(result.get(key), str)), None)
            validation_status = result.get("validation_status") if result and isinstance(result.get("validation_status"), str) else None
            execution_status = lifecycle_status if lifecycle_status in {"completed", "failed", "timed_out", "cancelled"} else (
                "skipped" if lifecycle_status == "skipped" else "missing_output" if selected else "not_started")
            statuses.append(module.UpstreamStatus(agent_id=agent_id, status=lifecycle_status, selected=selected,
                output_present=result is not None, execution_status=execution_status, llm_status=llm_status,
                analysis_status=analysis_status, validation_status=validation_status,
                output_status=str(analysis_status or "")[:40] or None))
            if not result or not selected:
                continue
            usable_outputs[agent_id] = result
            for ref in result.get("evidence", ()):
                try:
                    parsed = module.EvidenceReference.model_validate(ref)
                    store_evidence(parsed, agent_id)
                except Exception:
                    dropped_context_agents.add(agent_id)
                    continue
            if agent_id in category_agents:
                for row in result.get("findings", ()):
                    refs = []
                    refs_complete = True
                    for raw_ref in row.get("evidence", ())[:8]:
                        try:
                            parsed = module.EvidenceReference.model_validate(raw_ref)
                        except Exception:
                            refs_complete = False
                            continue
                        if len(row.get("evidence", ())) > 8:
                            dropped_context_agents.add(agent_id)
                        refs.append(parsed)
                        refs_complete = store_evidence(parsed, agent_id) and refs_complete
                    finding_id = row.get("finding_id")
                    if (not refs or not refs_complete or not isinstance(finding_id, str) or
                            any(f.finding_id == finding_id for f in findings)):
                        dropped_context_agents.add(agent_id)
                        continue
                    if len(findings) >= 400:
                        dropped_context_agents.add(agent_id)
                        continue
                    try:
                        category = row.get("category", "other")
                        category = getattr(category, "value", category)
                        if category not in {"structure", "business_logic", "quality", "security", "migration", "patch", "test", "review", "other"}:
                            category = {"structure_analyzer": "structure", "business_logic_analyst": "business_logic",
                                "code_quality_analyst": "quality", "security_analyst": "security"}.get(agent_id, "other")
                        findings.append(module.TestFinding(agent_id=agent_id, finding_id=finding_id,
                            category=category, title=str(row.get("title", "Finding"))[:300],
                            description=str(row.get("description", ""))[:4000], severity=row.get("severity", "info"),
                            confidence=row.get("confidence", 0.5), verification_status=row.get("verification_status", "unverified"),
                            evidence=tuple(refs), related_files=tuple(row.get("related_files", ()))[:50],
                            suggested_next_action=row.get("suggested_next_action", row.get("recommendation"))))
                    except Exception:
                        dropped_context_agents.add(agent_id)
                        continue
            if agent_id == "migration_planner":
                for row in result.get("tasks", ()):
                    refs = tuple(row.get("evidence_ids", ()))
                    if not refs or not set(refs) <= set(evidence):
                        dropped_context_agents.add(agent_id)
                        continue
                    if len(tasks) >= 500:
                        dropped_context_agents.add(agent_id)
                        continue
                    try:
                        tasks.append(module.MigrationTaskReference(task_id=row["task_id"], title=str(row["title"])[:240],
                            rationale=str(row["rationale"])[:2000], evidence_ids=refs,
                            affected_files=tuple(row.get("affected_files", ()))[:50],
                            priority=row.get("priority"), risk=row.get("risk"),
                            depends_on=tuple(row.get("depends_on", ()))[:50],
                            upstream_finding_ids=tuple(row.get("upstream_finding_ids", ()))[:50],
                            expected_outcome=row.get("expected_outcome"),
                            verification_criteria=tuple(row.get("verification_criteria", ()))[:20],
                            breaking_changes=tuple(row.get("breaking_changes", ()))[:20],
                            rollback_strategy=row.get("rollback_strategy"), phase_id=row.get("phase_id"),
                            sequence=row.get("sequence")))
                    except Exception:
                        dropped_context_agents.add(agent_id)
                        continue
        statuses = [row.model_copy(update={"context_truncated": row.agent_id in dropped_context_agents}) for row in statuses]
        output_limits = {
            "business_logic_analyst": {"workflows": 30, "business_rules": 40, "entities": 40,
                "relationships": 40, "uncertainties": 30},
            "code_quality_analyst": {"complexity_metrics": 60, "function_metrics": 120,
                "recommendations": 30, "uncertainties": 30, "limitations": 30},
            "security_analyst": {"assessments": 100, "insights": 40, "recommendations": 40,
                "uncertainties": 30, "limitations": 30},
            "migration_planner": {"tasks": 500, "risks": 50, "declared_dependencies": 100,
                "internal_dependencies": 100, "uncertainties": 30, "limitations": 30},
            "patch_author": {"proposals": 30, "source_findings": 100, "uncertainties": 30}}
        for upstream_id, limits in output_limits.items():
            output = usable_outputs.get(upstream_id, {})
            if any(len(output.get(key, ())) > limit for key, limit in limits.items()):
                dropped_context_agents.add(upstream_id)
        def claim(row):
            refs = tuple(row.get("evidence_ids", ()))
            return {"claim_id": row.get("claim_id"), "title": str(row.get("title", ""))[:240],
                "description": str(row.get("description", ""))[:1200], "reasoning": row.get("reasoning"),
                "evidence_ids": refs,
                "uncertainty": row.get("uncertainty")}
        def ref_ids(row):
            return tuple(ref.get("evidence_id") for ref in row.get("evidence", ()) if isinstance(ref, dict) and ref.get("evidence_id"))[:12]
        def output_evidence_ids(agent_id):
            return tuple(ref.get("evidence_id") for ref in usable_outputs.get(agent_id, {}).get("evidence", ())
                         if isinstance(ref, dict) and ref.get("evidence_id") in evidence)[:100]
        business = usable_outputs.get("business_logic_analyst", {})
        business_context = module.BusinessContext(
            executive_overview=str(business.get("executive_overview", ""))[:2500],
            domain_summary=str(business.get("domain_summary", ""))[:2500],
            workflows=tuple({"workflow_id": row.get("workflow_id"), "name": str(row.get("name", ""))[:240],
                "description": str(row.get("description", ""))[:1600], "steps": tuple(row.get("steps", ()))[:20],
                "evidence_ids": tuple(row.get("evidence_ids", ()))[:20]} for row in business.get("workflows", ())[:30]
                if set(row.get("evidence_ids", ())) <= set(evidence)),
            business_rules=tuple(claim(row) for row in business.get("business_rules", ())[:40]
                if set(row.get("evidence_ids", ())) <= set(evidence)),
            entities=tuple(claim(row) for row in business.get("entities", ())[:40]
                if set(row.get("evidence_ids", ())) <= set(evidence)),
            relationships=tuple({"relationship_id": row.get("relationship_id"),
                "source_entity": str(row.get("source_entity", ""))[:180], "target_entity": str(row.get("target_entity", ""))[:180],
                "relationship": str(row.get("relationship", ""))[:500], "evidence_ids": tuple(row.get("evidence_ids", ()))[:12]}
                for row in business.get("relationships", ())[:40] if set(row.get("evidence_ids", ())) <= set(evidence)),
            evidence_ids=output_evidence_ids("business_logic_analyst"),
            uncertainties=tuple(str(value)[:1000] for value in business.get("uncertainties", ())[:30]))
        quality = usable_outputs.get("code_quality_analyst", {})
        quality_context = module.QualityContext(overview=str(quality.get("overview", ""))[:2500],
            analysis_coverage=quality.get("coverage", "partial"),
            metrics=tuple({"path": row.get("path"), "function_count": row.get("function_count", 0),
                "class_count": row.get("class_count", 0), "average_cyclomatic_complexity": row.get("average_cyclomatic_complexity", 1),
                "maximum_cyclomatic_complexity": row.get("maximum_cyclomatic_complexity", 1),
                "maximum_nesting_depth": row.get("maximum_nesting_depth", 0),
                "missing_public_annotations": row.get("missing_public_annotations", 0), "evidence_ids": ref_ids(row)}
                for row in quality.get("complexity_metrics", ())[:60] if ref_ids(row) and set(ref_ids(row)) <= set(evidence)),
            function_metrics=tuple({"path": row.get("path"), "qualified_name": row.get("qualified_name"),
                "start_line": row.get("start_line"), "end_line": row.get("end_line"), "length": row.get("length"),
                "cyclomatic_complexity": row.get("cyclomatic_complexity"), "nesting_depth": row.get("nesting_depth"),
                "parameter_count": row.get("parameter_count"), "missing_annotations": row.get("missing_annotations"),
                "evidence_ids": ref_ids(row)} for row in quality.get("function_metrics", ())[:120]
                if ref_ids(row) and set(ref_ids(row)) <= set(evidence)),
            recommendations=tuple({"recommendation_id": row.get("recommendation_id"), "action": str(row.get("action", ""))[:1000], "rationale": str(row.get("rationale", ""))[:1000],
                "finding_ids": tuple(row.get("finding_ids", ()))[:20], "evidence_ids": tuple(row.get("evidence_ids", ()))[:20]}
                for row in quality.get("recommendations", ())[:30]
                if set(row.get("finding_ids", ())) <= {f.finding_id for f in findings} and set(row.get("evidence_ids", ())) <= set(evidence)),
            evidence_ids=output_evidence_ids("code_quality_analyst"),
            uncertainties=tuple(str(value)[:1000] for value in quality.get("uncertainties", ())[:30]),
            limitations=tuple(str(value)[:1000] for value in quality.get("limitations", ())[:30]))
        security = usable_outputs.get("security_analyst", {})
        security_context = module.SecurityContext(overview=str(security.get("overview", ""))[:2000],
            analysis_coverage=security.get("coverage", "partial"), scanned_files=security.get("scanned_files", 0),
            assessments=tuple({"finding_id": row.get("finding_id"), "assessment": row.get("assessment"),
                "rationale": str(row.get("rationale", ""))[:1000], "uncertainty": str(row.get("uncertainty", ""))[:800]}
                for row in security.get("assessments", ())[:100]
                if row.get("finding_id") in {f.finding_id for f in findings}),
            insights=tuple({"insight_id": row.get("insight_id"), "kind": row.get("kind"),
                "title": str(row.get("title", ""))[:240], "explanation": str(row.get("explanation", ""))[:1200],
                "finding_ids": tuple(row.get("finding_ids", ()))[:20], "evidence_ids": tuple(row.get("evidence_ids", ()))[:20],
                "uncertainty": str(row.get("uncertainty", ""))[:800]}
                for row in security.get("insights", ())[:40]
                if set(row.get("finding_ids", ())) <= {f.finding_id for f in findings} and set(row.get("evidence_ids", ())) <= set(evidence)),
            recommendations=tuple({"recommendation_id": row.get("recommendation_id"), "action": str(row.get("action", ""))[:1000], "rationale": str(row.get("rationale", ""))[:1000],
                "finding_ids": tuple(row.get("finding_ids", ()))[:20], "evidence_ids": tuple(row.get("evidence_ids", ()))[:20]}
                for row in security.get("recommendations", ())[:40]
                if set(row.get("finding_ids", ())) <= {f.finding_id for f in findings} and set(row.get("evidence_ids", ())) <= set(evidence)),
            evidence_ids=output_evidence_ids("security_analyst"),
            uncertainties=tuple(str(value)[:1000] for value in security.get("uncertainties", ())[:30]),
            limitations=tuple(str(value)[:1000] for value in security.get("limitations", ())[:30]))
        migration = usable_outputs.get("migration_planner", {})
        db = migration.get("database_consideration") or {}
        api = migration.get("api_consideration") or {}
        migration_context = module.MigrationContext(plan_status=migration.get("plan_status", "incomplete"),
            current_state_assessment=str(migration.get("current_state_assessment", ""))[:2500],
            target_state=migration.get("target_state"), target_state_uncertainty=str(migration.get("target_state_uncertainty", ""))[:800],
            risks=tuple({"risk_id": row.get("risk_id"), "title": str(row.get("title", ""))[:240],
                "description": str(row.get("description", ""))[:1200], "severity": str(row.get("severity", "info")),
                "likelihood": row.get("likelihood", "unknown"), "upstream_finding_ids": tuple(row.get("upstream_finding_ids", ()))[:30],
                "mitigation": str(row.get("mitigation", ""))[:1000],
                "evidence_ids": tuple(row.get("evidence_ids", ()))[:20], "uncertainty": str(row.get("uncertainty", ""))[:800]}
                for row in migration.get("risks", ())[:50] if set(row.get("evidence_ids", ())) <= set(evidence)),
            declared_dependencies=tuple({"ecosystem": row.get("ecosystem"), "name": row.get("name"),
                "version_spec": row.get("version_spec"), "exact_version": row.get("exact_version"),
                "evidence_ids": tuple(row.get("evidence_ids", ()))[:10]}
                for row in migration.get("declared_dependencies", ())[:100]
                if row.get("evidence_ids") and set(row.get("evidence_ids", ())) <= set(evidence)),
            internal_dependencies=tuple({"source_module": row.get("source_module"), "target_module": row.get("target_module"),
                "evidence_id": row.get("evidence_id")}
                for row in migration.get("internal_dependencies", ())[:100]
                if row.get("evidence_id") in evidence),
            evidence_ids=output_evidence_ids("migration_planner"),
            database_assessment=str(db.get("assessment", ""))[:1000] or None,
            database_data_migration_required=db.get("data_migration_required"),
            api_assessment=str(api.get("assessment", ""))[:1000] or None,
            api_breaking_change_risks=tuple(str(value)[:500] for value in api.get("breaking_change_risks", ())[:20]),
            uncertainties=tuple(str(value)[:1000] for value in migration.get("uncertainties", ())[:30]),
            limitations=tuple(str(value)[:1000] for value in migration.get("limitations", ())[:30]))
        patch_result = usable_outputs.get("patch_author", {})
        patch_status = patch_result.get("status", "missing")
        proposals = []
        if patch_status == "awaiting_review" and patch_result.get("validation_status") == "passed":
            for row in patch_result.get("proposals", ()):
                try:
                    proposal_fields = ("proposal_id", "path", "original_content_hash", "proposed_content_hash",
                        "unified_diff", "related_finding_ids", "migration_task_ids", "evidence_ids", "rationale",
                        "expected_behavior", "risk", "compatibility_assessment", "required_tests", "uncertainty")
                    proposal = module.PatchProposalReference.model_validate({
                        **{key: row[key] for key in proposal_fields}, "status": "awaiting_review",
                        "validation_status": "passed", "change_summary": str(row.get("expected_behavior", ""))[:1200]})
                    proposals.append(proposal)
                    for eid in proposal.evidence_ids:
                        # Agent 6 emits its full evidence index; missing IDs are rejected by the input schema.
                        if eid not in evidence:
                            raise ValueError("patch proposal evidence missing")
                except Exception:
                    dropped_context_agents.add("patch_author")
                    proposals.clear()
                    break
        else:
            proposals = []
        if patch_status not in {"awaiting_review", "abstained", "unavailable", "rejected"}:
            patch_status = "missing"
        patch_source_findings = []
        for row in patch_result.get("source_findings", ())[:100]:
            ids = ref_ids(row)
            finding_id = row.get("finding_id")
            agent_id = row.get("agent_id")
            if ids and set(ids) <= set(evidence) and any(f.finding_id == finding_id for f in findings):
                try:
                    patch_source_findings.append(module.PatchSourceFindingContext(finding_id=finding_id,
                        agent_id=agent_id, title=str(row.get("title", ""))[:240], evidence_ids=ids))
                except Exception:
                    dropped_context_agents.add("patch_author")
                    continue
        patch_status_value = patch_status if patch_status in {"awaiting_review", "abstained", "unavailable", "rejected"} else "missing"
        patch_context = module.PatchContext(repository_id=workflow_request.repository_id,
            snapshot_id=workflow_request.snapshot_id, commit_sha=workflow_request.commit_sha,
            manifest_digest=workflow_request.manifest_digest, summary=str(patch_result.get("summary", ""))[:2000],
            llm_status=patch_result.get("llm_status") if patch_result.get("llm_status") in {"completed", "unavailable", "failed", "not_attempted"} else None,
            validation_status=patch_result.get("validation_status") if patch_result.get("validation_status") in {"passed", "not_run", "rejected"} else None,
            proposal_status=patch_status_value,
            source_findings=tuple(patch_source_findings), affected_paths=tuple(sorted({p.path for p in proposals}))[:100],
            evidence_ids=output_evidence_ids("patch_author"),
            uncertainties=tuple(str(value)[:1000] for value in patch_result.get("uncertainties", ())[:30]))
        statuses = [row.model_copy(update={"context_truncated": row.agent_id in dropped_context_agents}) for row in statuses]
        kwargs = {"execution_id": uuid5(workflow_request.execution_id, "test_engineer"),
            "workspace_id": workflow_request.workspace_id, "repository_id": workflow_request.repository_id,
            "snapshot_id": workflow_request.snapshot_id, "commit_sha": workflow_request.commit_sha,
            "manifest_digest": workflow_request.manifest_digest, "upstream_statuses": tuple(statuses),
            "business_context": business_context, "quality_context": quality_context,
            "security_context": security_context, "migration_context": migration_context, "patch_context": patch_context,
            "findings": tuple(findings), "migration_tasks": tuple(tasks), "patch_status": patch_status,
            "patch_proposals": tuple(proposals), "evidence": tuple(evidence.values()),
            "upstream_truncated": len(findings) >= 400 or len(tasks) >= 500}
        if options:
            kwargs["options"] = options
        return module.TestEngineerInput(**kwargs)
    def make_input(workflow_request, prior_results):
        return make_input_with_state(workflow_request, prior_results, {})
    return AgentNodeBinding(input_factory=make_input, context_factory=context_factory,
                            input_factory_with_state=make_input_with_state)


def review_agent_binding(context_factory):
    bind_review_input = import_module("agents.08_review_agent.services.input_binding").bind_review_input
    return AgentNodeBinding(input_factory=lambda workflow_request, prior_results:
        bind_review_input(workflow_request, prior_results, {}), context_factory=context_factory,
        input_factory_with_state=bind_review_input)
