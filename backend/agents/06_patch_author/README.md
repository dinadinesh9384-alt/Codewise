# Agent 6: Patch Author

`patch_author` creates review-only patch proposals from evidence-backed Agent 1–5 findings and migration tasks. It uses the shared LangChain gateway and **Pool B**. In LangGraph it runs only when selected, after the quality/planning join, and requires mandatory Agents 1 and 2 plus every selected optional upstream agent to have completed.

The input binding marks each upstream agent `completed`, `skipped`, or `unavailable`, and supplies only completed results. Agent 6 reads only authorized snapshot paths supported by upstream evidence or tasks. It skips protected configuration and authorization paths, private key and environment files, secret-bearing source, instruction-like source, oversized content, and unsupported binary or non UTF-8 files. Source and prompts are bounded; no repository code is imported or executed.

The LLM returns structured per-file unified diffs. A deterministic in-memory validator checks same-path headers, safe relative paths, protected path policy, exact original line context, hunk line counts and order, duplicate or overlapping changes, source hashes, finding/task/evidence links, and the proposed content hash. Invalid model output is discarded. Proposals contain diffs and hashes only; no artifact is written and no snapshot or working tree is modified.

Every proposal is marked `awaiting_review`, `approved=false`, and `applied=false`. Agent 6 requires `patch.propose` but never requests `patch.apply` or GitHub publishing permission. The existing separate LangGraph approval interrupt remains responsible for approval when the review agent is available and selected; approval does not apply a patch.

## Limits

The validator supports UTF-8 text modifications to existing files listed in the sealed manifest. It rejects binary changes, new or deleted files, renames, secret-bearing source files, and protected configuration/security paths. It does not execute tests or claim runtime validation. When no relevant evidence or safe bounded source is available, it abstains.
