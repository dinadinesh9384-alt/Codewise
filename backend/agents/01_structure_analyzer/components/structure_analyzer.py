"""Pure composition entry for structural observations and stable findings."""

from hashlib import sha256

from agent_foundation.contracts.findings import Finding, FindingCategory, Severity, VerificationStatus
from ..schemas.repository import StructuralObservation


def observe_structure(paths, technologies, configurations, entries, languages, evidence_for):
    pathset = set(paths)
    observations = []
    def add(category, text, supporting=(), confidence="medium"):
        unique_paths = tuple(sorted(set(supporting)))
        refs = tuple(evidence_for(path, "structure_observer") for path in unique_paths)
        observations.append(StructuralObservation(category=category, observation=text, confidence=confidence,
            evidence=refs, supporting_paths=unique_paths))
    roots = {p.split("/", 1)[0] for p in pathset}
    if "frontend" in roots and "backend" in roots:
        frontend_path = next((path for path in sorted(pathset) if path.split("/", 1)[0] == "frontend"), None)
        backend_path = next((path for path in sorted(pathset) if path.split("/", 1)[0] == "backend"), None)
        add("application_layout", "Separate top-level frontend and backend directories are present.",
            tuple(path for path in (frontend_path, backend_path) if path))
    tests = tuple(path for path in sorted(pathset) if any(part.casefold() in {"test", "tests", "__tests__", "spec"} for part in path.split("/")))
    if tests: add("testing", "Test-named directories or files are present.", tests[:3])
    migrations = tuple(path for path in sorted(pathset) if "migrations" in {part.casefold() for part in path.split("/")})
    if migrations: add("database", "Migration-related paths are present.", migrations[:3])
    docs = tuple(path for path in sorted(pathset) if path.split("/", 1)[0].casefold() in {"docs", "documentation"})
    if docs: add("documentation", "A documentation directory is present.", docs[:2])
    api_paths = tuple(path for path in sorted(pathset) if any(part.casefold() in {"api", "routes", "controllers"}
                      for part in path.split("/")))
    if api_paths: add("api_structure", "API-, route-, or controller-named paths are present; endpoint behavior was not analyzed.", api_paths[:3])
    ecosystems = {"JavaScript/TypeScript" if item.language in {"JavaScript", "TypeScript", "Vue", "Svelte"} else item.language
                  for item in languages if item.language != "Unknown"}
    if len(ecosystems) > 1:
        supporting = tuple(ref.path for stat in languages for ref in stat.evidence[:1] if ref.path)
        add("ecosystems", "Multiple language groups are represented in the repository inventory.", supporting[:4])
    if technologies:
        infra = tuple(item for item in technologies if item.category in {"containerization", "CI/CD", "infrastructure as code"})
        for technology in infra:
            add("delivery_infrastructure", f"{technology.name} configuration is present.",
                tuple(ref.path for ref in technology.evidence if ref.path), technology.confidence)
    if configurations:
        add("configuration", f"{len(configurations)} recognized configuration or dependency manifest files are present.",
            tuple(item.path for item in configurations[:3]))
    return tuple(observations)


def build_findings(technologies, entries, observations):
    findings = []
    candidates = [("Technology detected: " + item.name, item.explanation, item.evidence, Severity.INFO)
                  for item in technologies]
    candidates.extend(("Probable entry point: " + item.path, item.explanation, item.evidence, Severity.INFO)
                      for item in entries)
    candidates.extend((item.observation, item.observation, item.evidence, Severity.INFO) for item in observations)
    for title, description, refs, severity in candidates:
        if not refs: continue
        stable = sha256((title + "|" + "|".join(ref.path or "" for ref in refs)).encode()).hexdigest()[:32]
        findings.append(Finding(finding_id=stable, category=FindingCategory.STRUCTURE, title=title[:300],
            description=description, severity=severity, confidence=.85, evidence=refs,
            related_files=tuple(ref.path for ref in refs if ref.path),
            verification_status=VerificationStatus.EVIDENCE_SUPPORTED,
            uncertainty="Structural evidence supports this observation; it does not establish runtime behavior."))
    return tuple(findings)
