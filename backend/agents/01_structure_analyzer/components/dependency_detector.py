"""Bounded parsers for common dependency declaration formats; never resolves packages."""

import json
import re
import tomllib
import xml.etree.ElementTree as ET

from ..schemas.repository import Dependency, DependencyManifest

_REQ = re.compile(r"^\s*(-e\s+)?([A-Za-z0-9_.-]+)(?:\[([^]]+)\])?\s*(===|==|~=|!=|<=|>=|<|>)?\s*([^;\s#]+)?")
_GRADLE = re.compile(r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|developmentOnly)\s*\(?\s*[\"']([^\"']+):([^:\"']+):([^\"']+)[\"']")
_GEM = re.compile(r"^\s*gem\s+[\"']([^\"']+)[\"'](?:\s*,\s*[\"']([^\"']+)[\"'])?", re.M)
_GO_REQUIRE = re.compile(r"^\s*([^\s()]+)\s+(v[^\s]+)", re.M)
_SECRET = re.compile(r"(?:https?://[^/\s:@]+:[^@\s]+@|(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}|(?:password|secret|token|api[_-]?key)\s*[:=]|-----BEGIN|^[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}$)", re.I)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9@_./:+-]{1,200}$")
_SAFE_CONSTRAINT = re.compile(r"^[A-Za-z0-9*<>=!~^.,|+() _:-]{1,256}$")


def _pairs(data, group="default"):
    if not isinstance(data, dict): return []
    output = []
    for name, version in sorted(data.items(), key=lambda item: str(item[0]).casefold()):
        safe_name = _safe_name(str(name))
        if safe_name is None: continue
        output.append(Dependency(name=safe_name, constraint=_safe_constraint(version), group=_safe_group(group)))
    return output


def _safe_name(value):
    return value if _SAFE_NAME.fullmatch(value) and not _SECRET.search(value) else None


def _safe_group(value):
    return value if re.fullmatch(r"[A-Za-z0-9_@.-]{1,128}", str(value)) and not _SECRET.search(str(value)) else "other"


def _safe_constraint(value):
    if value in (None, ""): return None
    value = str(value)
    if len(value) > 256 or not _SAFE_CONSTRAINT.fullmatch(value) or _SECRET.search(value): return None
    if re.search(r"[A-Za-z0-9_-]{40,}", value): return None
    return value


def _mapping(value, field_name):
    if value is None: return {}
    if not isinstance(value, dict): raise ValueError(f"{field_name} must be a table/object")
    return value


def _array(value, field_name):
    if value is None: return []
    if not isinstance(value, list): raise ValueError(f"{field_name} must be an array")
    return value


def parse_dependencies(path, content: bytes):
    name = path.rsplit("/", 1)[-1].casefold()
    text = content.decode("utf-8-sig", errors="strict")
    parsed = []
    if name == "requirements.txt":
        for line in text.splitlines():
            match = _REQ.match(line)
            if not match or line.lstrip().startswith(("#", "-")):
                continue
            package, extras, operator, version = match.group(2), match.group(3), match.group(4), match.group(5)
            safe_name = _safe_name(package)
            if safe_name:
                parsed.append(Dependency(name=safe_name,
                    constraint=_safe_constraint((operator or "") + version if version else None), group="default"))
    elif name in {"pyproject.toml", "pipfile", "cargo.toml"}:
        data = tomllib.loads(text)
        if name == "pyproject.toml":
            project = _mapping(data.get("project", {}), "project")
            for req in _array(project.get("dependencies", []), "project.dependencies"):
                parsed.extend(_parse_pep508(str(req), "default"))
            for group, deps in sorted(_mapping(project.get("optional-dependencies", {}), "project.optional-dependencies").items()):
                for req in _array(deps, f"project.optional-dependencies.{group}"):
                    parsed.extend(_parse_pep508(str(req), group))
            tool = _mapping(data.get("tool", {}), "tool")
            poetry = _mapping(tool.get("poetry", {}), "tool.poetry")
            for group, deps in (("default", _mapping(poetry.get("dependencies", {}), "poetry.dependencies")),
                                ("development", _mapping(poetry.get("dev-dependencies", {}), "poetry.dev-dependencies"))):
                parsed.extend(_pairs({k:v for k,v in deps.items() if k.casefold() != "python"}, group))
            for group, value in sorted(_mapping(poetry.get("group", {}), "poetry.group").items()):
                value = _mapping(value, f"poetry.group.{group}")
                parsed.extend(_pairs(_mapping(value.get("dependencies", {}), f"poetry.group.{group}.dependencies"), group))
        elif name == "pipfile":
            parsed.extend(_pairs(_mapping(data.get("packages", {}), "packages")))
            parsed.extend(_pairs(_mapping(data.get("dev-packages", {}), "dev-packages"), "development"))
        else:
            for group in ("dependencies", "dev-dependencies", "build-dependencies"):
                parsed.extend(_pairs(_mapping(data.get(group, {}), group), group.replace("-dependencies", "")))
            for target, table in sorted(_mapping(data.get("target", {}), "target").items()):
                table = _mapping(table, f"target.{target}")
                parsed.extend(_pairs(_mapping(table.get("dependencies", {}), f"target.{target}.dependencies"), "target:" + target))
    elif name == "package.json":
        data = json.loads(text)
        if not isinstance(data, dict): raise ValueError("manifest root must be an object")
        parsed.extend(_pairs(_mapping(data.get("dependencies", {}), "dependencies")))
        for group in ("devDependencies", "optionalDependencies", "peerDependencies"):
            parsed.extend(_pairs(_mapping(data.get(group, {}), group), group))
    elif name == "composer.json":
        data = json.loads(text)
        if not isinstance(data, dict): raise ValueError("manifest root must be an object")
        parsed.extend(_pairs(_mapping(data.get("require", {}), "require")))
        parsed.extend(_pairs(_mapping(data.get("require-dev", {}), "require-dev"), "development"))
    elif name == "pom.xml":
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
            raise ValueError("DTD and entity declarations are not supported")
        root = ET.fromstring(text)
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "dependency": continue
            fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in element}
            if fields.get("groupId") and fields.get("artifactId"):
                dep_name = _safe_name(fields["groupId"] + ":" + fields["artifactId"])
                if dep_name:
                    parsed.append(Dependency(name=dep_name, constraint=_safe_constraint(fields.get("version")),
                        group="development" if fields.get("scope") in {"test", "provided"} else "default"))
    elif name.endswith(".csproj"):
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
            raise ValueError("DTD and entity declarations are not supported")
        root = ET.fromstring(text)
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] in {"PackageReference", "FrameworkReference"}:
                dep_name = _safe_name(element.attrib.get("Include", ""))
                version = element.attrib.get("Version")
                if dep_name: parsed.append(Dependency(name=dep_name, constraint=_safe_constraint(version), group="default"))
    elif name in {"build.gradle", "build.gradle.kts"}:
        for line in text.splitlines():
            match = _GRADLE.search(line)
            if match:
                config = re.match(r"\s*([A-Za-z]+)", line)
                dep_name = _safe_name(match.group(1) + ":" + match.group(2))
                if dep_name:
                    group = "development" if config and config.group(1).startswith("test") else "default"
                    parsed.append(Dependency(name=dep_name, constraint=_safe_constraint(match.group(3)), group=group))
    elif name == "go.mod":
        lines = text.splitlines()
        in_require = False
        for line in lines:
            if line.strip().startswith("require ("): in_require = True; continue
            if in_require and line.strip() == ")": in_require = False; continue
            candidate = line.strip()
            if candidate.startswith("require ") and not candidate.startswith("require ("):
                candidate = candidate[len("require "):]
            match = _GO_REQUIRE.match(candidate)
            if match and (in_require or line.strip().startswith("require ")):
                dep_name = _safe_name(match.group(1))
                if dep_name:
                    parsed.append(Dependency(name=dep_name, constraint=_safe_constraint(match.group(2)), group="default",
                                             direct="// indirect" not in line))
    elif name == "gemfile":
        group = "default"
        for line in text.splitlines():
            group_match = re.match(r"\s*group\s+(:[A-Za-z0-9_]+)", line)
            if group_match:
                group = "development" if group_match.group(1) in {":development", ":test"} else group_match.group(1)[1:]
            gem_match = _GEM.match(line)
            if gem_match:
                safe = _safe_name(gem_match.group(1))
                if safe: parsed.append(Dependency(name=safe, constraint=_safe_constraint(gem_match.group(2)), group=group))
            if line.strip() == "end": group = "default"
    unique = {(d.name, d.constraint, d.group, d.direct): d for d in parsed}
    return tuple(sorted(unique.values(), key=lambda d: (d.group, d.name.casefold(), d.constraint or "")))


def _parse_pep508(value, group):
    match = re.match(r"\s*([A-Za-z0-9_.-]+)\s*(?:\[[^]]+\])?\s*(.*)", value)
    if not match: return []
    safe_name = _safe_name(match.group(1))
    if not safe_name: return []
    constraint = _safe_constraint(match.group(2).strip() or None)
    return [Dependency(name=safe_name, constraint=constraint, group=_safe_group(group))]


def detect_dependency_manifests(records, contents, evidence_for):
    result = []
    supported = {"requirements.txt", "pyproject.toml", "pipfile", "package.json", "pom.xml", "build.gradle",
                 "build.gradle.kts", "cargo.toml", "go.mod", "composer.json", "gemfile"}
    for record in records:
        path = record["path"]
        manifest_name = path.rsplit("/", 1)[-1].casefold()
        if manifest_name not in supported and not manifest_name.endswith(".csproj"): continue
        content = contents.get(path)
        if content is None:
            status, dependencies = "skipped_by_content_limit", ()
        else:
            try:
                dependencies = parse_dependencies(path, content)
                lines = content.decode("utf-8-sig", errors="replace").splitlines()
                enriched = []
                for dependency in dependencies:
                    token = dependency.name.rsplit(":", 1)[-1].rsplit("/", 1)[-1].casefold()
                    line_number = next((index for index, line in enumerate(lines, 1) if token in line.casefold()), None)
                    reference = (evidence_for(path, "dependency_detector", line_number, line_number)
                                 if line_number else evidence_for(path, "dependency_detector"))
                    enriched.append(dependency.model_copy(update={"evidence": reference}))
                dependencies = tuple(enriched)
                status = "parsed"
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, ET.ParseError, tomllib.TOMLDecodeError):
                dependencies, status = (), "malformed"
        result.append(DependencyManifest(ecosystem=_ecosystem(path), manifest_path=path, dependencies=dependencies,
            parse_status=status, evidence=evidence_for(path, "dependency_detector")))
    return tuple(result)


def _ecosystem(path):
    name = path.rsplit("/", 1)[-1].casefold()
    return {"requirements.txt":"python", "pyproject.toml":"python", "pipfile":"python", "package.json":"javascript",
            "pom.xml":"java", "build.gradle":"java", "build.gradle.kts":"java", "cargo.toml":"rust",
            "go.mod":"go", "composer.json":"php", "gemfile":"ruby"}.get(name, "dotnet")
