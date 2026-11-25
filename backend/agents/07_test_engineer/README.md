# Agent 7: Test Engineer

Agent 7 inventories test and test-configuration files in the authorized immutable snapshot, then uses the shared LangChain model adapter for evidence-constrained test planning. Its stable ID is `test_engineer`; the LLM gateway routes it through Pool C. Its optional selection remains the existing patch-engineering group containing Agents 6 and 7.

Inventory is static. Python test syntax is parsed with `ast` without importing modules. Existing coverage is reported only when a snapshot contains a parseable coverage JSON/XML report. Other language test files are inventoried with limited syntax support and stated limitations. Agent 6 proposals are checked against the snapshot bytes and validated diff hashes in memory; this is not a runtime correctness check.

The input binding carries bounded typed context from Agents 2–6 and records graph lifecycle, execution, LLM, analysis, and validation states separately. Missing or unavailable upstream reasoning is represented as unknown/partial context, not evidence that testing is unnecessary. Scenario IDs and evidence/finding/task/patch references are checked before output.

Coverage percentages come only from parseable reports in the snapshot. They are marked `snapshot_report_only` and `independently_verified=false`; model prose cannot introduce numeric coverage claims. Model prose that claims tests were run or passed/failed is rejected. Other semantic prose is reference checked and explicitly marked `references_validated_semantics_unverified` because arbitrary natural language cannot be fully proven deterministically. Scenario execution status is always `not_executed`.

Agent 7 requests no test execution, sandbox execution, patch application, or repository write permission, and the graph fails closed if `TESTS_EXECUTE` is present in its context. It does not write generated tests; output contains reviewable specifications only. No scenario is marked as passed.
