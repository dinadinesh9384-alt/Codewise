# Agent 8: Review Agent

`review_agent` is an optional final reviewer in the existing `final_review` group. It uses Pool C and the shared LangChain `GatewayChatModel` for structured reasoning. Its bounded input is built only from schema-validated Agent 1–7 outputs with matching repository, snapshot, commit, manifest, and per-agent execution identity.

The binding includes Agent 1's repository inventory and limitations; Agent 2's workflows, rules, entities, relationships, findings, and uncertainty; Agent 3's findings, metrics, recommendations, and limitations; Agent 4's security findings, assessments, remediation context, and uncertainty; Agent 5's migration tasks, dependencies, risks, constraints, and priorities; Agent 6's validated proposals and source finding relationships; and Agent 7's prioritized scenarios, evidence, patch associations, and explicit `not_executed` status. Optional upstream gaps, skipped or failed lifecycle states, partial analyses, unavailable LLM reasoning, invalid output, and missing output remain visible in status fields. Missing analysis is never treated as approval or evidence of safety.

Before model reasoning, Agent 8 checks cross-output IDs, snapshot evidence identity, referenced file hashes through the authorized snapshot reader, migration task dependencies, patch source references, and Agent 6 diffs/hashes using Agent 6's in-memory validator. It never applies a diff. The model can return assessments, inferred risks, unresolved questions, consistency checks, and corrective actions with supplied references. Deterministic validation rejects unknown references and claims that tests ran/passed, patches were approved, or unsupported findings were verified. Agent 7 scenarios remain proposals and are always reported as not executed.

Review outcomes:

- `ready_for_human_review`: mandatory and optional analysis is present, complete, and deterministic checks found no warnings. This is not approval.
- `ready_with_warnings`: analysis is complete but review assessments or unverifiable evidence need human attention.
- `changes_required`: deterministic integrity/patch checks failed or the review identified a corrective action.
- `blocked`: mandatory Agent 1 or Agent 2 output is absent or failed.
- `incomplete`: optional or partial upstream analysis, truncation, or unavailable model reasoning prevents a complete review.

The human approval interrupt remains a separate LangGraph stage after Agent 8. Agent 8 has read-only repository and reasoning permissions; it cannot execute tests or repository code, install packages, write files, apply patches, publish changes, or approve on behalf of a person.
