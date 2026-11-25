"""Bounded AST security checks. Repository source is parsed as data and never imported or run."""

import ast
import re


_SECRET_LINE = re.compile(
    r"(?i)\b(api[_-]?key|client[_-]?secret|secret[_-]?key|password|passwd|access[_-]?token|private[_-]?key)"
    r"\b\s*[:=]\s*(['\"]?)([A-Za-z0-9_./+=-]{12,})"
)
_PLACEHOLDERS = {"changeme", "change_me", "your_api_key", "your-secret", "example-secret",
                 "replace-me", "placeholder", "not-a-secret", "dummy-secret", "test-secret"}
_SOURCE_ATTRS = {"GET", "POST", "args", "form", "json", "data", "query_params"}
_SQL_SINKS = {"execute", "executemany", "raw", "extra"}
_COMMAND_SINKS = {"system", "popen", "Popen", "run", "call", "check_call", "check_output"}
_PATH_SINKS = {"open", "read_text", "read_bytes", "write_text", "write_bytes", "send_file", "FileResponse"}


def inspect_python_security(source: str):
    """Return sanitized security observations from one Python source file."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return {"status": "malformed", "observations": []}

    lines = source.splitlines()
    observations = []
    seen = set()

    def add(classification, title, description, remediation, cwe, severity, confidence,
            sink_node, source_nodes=(), uncertainty=None):
        source_lines = sorted({line for item in source_nodes
                               for line in (item.lineno, getattr(item, "end_lineno", item.lineno))})
        sink_lines = {sink_node.lineno, getattr(sink_node, "end_lineno", sink_node.lineno)}
        line_numbers = sorted(set(source_lines[:1] + source_lines[-1:] + list(sink_lines)))
        key = (classification, title, tuple(line_numbers))
        if key in seen:
            return
        seen.add(key)
        observations.append({"classification": classification, "title": title,
            "description": description, "remediation": remediation, "cwe_id": cwe,
            "severity": severity, "confidence": confidence,
            "lines": line_numbers, "uncertainty": uncertainty,
            "source_lines": sorted({item.lineno for item in source_nodes})})

    for line_no, line in enumerate(lines, 1):
        match = _SECRET_LINE.search(line)
        if match:
            value = match.group(3).strip("'\"")
            normalized = value.casefold()
            if (len(value) >= 12 and normalized not in _PLACEHOLDERS and
                    not normalized.startswith(("${", "os.getenv", "getenv(", "env("))):
                add("hardcoded_credential", "Credential-shaped value is hardcoded",
                    "A credential-like assignment contains a non-placeholder literal. The literal value is intentionally omitted; its validity is unverified.",
                    "Remove the literal, rotate it if it may have been used, and load credentials from the approved secret store or environment.",
                    "CWE-798", "high", 0.86, _line_node(line_no))

    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
    for function in functions:
        for decorator in getattr(function, "decorator_list", ()):
            if _call_or_name(decorator).split(".")[-1] == "csrf_exempt":
                add("csrf_protection_disabled", "CSRF protection is explicitly exempted",
                    "A view or callable is marked csrf_exempt; whether another trusted control protects it is unknown.",
                    "Keep CSRF protection enabled for browser-authenticated state-changing requests or document and test an equivalent control.",
                    "CWE-352", "medium", 0.76, decorator,
                    uncertainty="The surrounding authentication and request flow were not verified.")
    scopes = [tree, *functions]
    for scope in scopes:
        body = scope.body if hasattr(scope, "body") else [scope]
        scoped = _scope_nodes(body)
        tainted_names = set()
        taint_sources = {}
        dynamic_names = set()
        for node in sorted((item for item in scoped if isinstance(item, (ast.Assign, ast.AnnAssign, ast.NamedExpr))),
                           key=lambda item: (item.lineno, item.col_offset)):
            value = getattr(node, "value", None)
            sources = _taint_sources(value, tainted_names, taint_sources)
            targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            for target in targets:
                if isinstance(target, ast.Name):
                    if sources:
                        tainted_names.add(target.id)
                        taint_sources[target.id] = [*sources, node]
                        if _is_dynamic(value) or (isinstance(value, ast.Name) and value.id in dynamic_names):
                            dynamic_names.add(target.id)
                        else:
                            dynamic_names.discard(target.id)
                    else:
                        tainted_names.discard(target.id)
                        taint_sources.pop(target.id, None)
                        dynamic_names.discard(target.id)

        for node in scoped:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value is True:
                if any(isinstance(target, ast.Name) and target.id == "DEBUG" for target in node.targets):
                    add("debug_mode_enabled", "Debug mode is enabled in source configuration",
                        "A Python DEBUG setting is assigned the literal True; deployment context is unknown.",
                        "Disable debug mode in production and select it only through a safe environment-specific configuration.",
                        "CWE-489", "medium", 0.8, node)
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_or_name(node.func)
            tail = call_name.split(".")[-1]
            if tail in {"load", "loads"} and any(part in {"pickle", "dill", "marshal", "yaml"}
                                                   for part in call_name.split(".")):
                if not (call_name.endswith("safe_load") or _has_safe_loader(node)):
                    add("unsafe_deserialization", "Potentially unsafe deserialization API is used",
                        f"`{tail}` is called through `{call_name[:120]}`; the trust boundary of the input is not established.",
                        "Use a safe data format or safe loader, and never deserialize attacker-controlled bytes with object construction enabled.",
                        "CWE-502", "high" if "pickle" in call_name or "dill" in call_name else "medium",
                        0.84, node, uncertainty="Input provenance and any serialization safeguards require review.")
            if tail in {"eval", "exec"} and node.args:
                sources = _taint_sources(node.args[0], tainted_names, taint_sources)
                if sources:
                    add("dynamic_code_execution", "Untrusted input reaches dynamic code execution",
                        "A value derived from a request or process input reaches a dynamic code execution function.",
                        "Remove dynamic evaluation or replace it with a fixed parser and an explicit allowlist.",
                        "CWE-95", "high", 0.91, node, sources,
                        uncertainty="Static local data flow does not model sanitizers or runtime dispatch.")
            if tail in {"execute", "executemany", "raw", "extra"} and node.args:
                query = node.args[0]
                sources = _taint_sources(query, tainted_names, taint_sources)
                if sources and (_is_dynamic(query) or
                                isinstance(query, ast.Name) and query.id in dynamic_names):
                    add("sql_injection_candidate", "Request-derived data is interpolated into a SQL operation",
                        "A value derived from a request or process input reaches a dynamically constructed SQL argument.",
                        "Use parameterized queries and keep SQL structure separate from user-controlled values.",
                        "CWE-89", "high", 0.89, node, sources,
                        uncertainty="Framework query semantics and any upstream validation were not executed or inferred.")
            if tail in _COMMAND_SINKS and node.args:
                sources = _taint_sources(node.args[0], tainted_names, taint_sources)
                shell_true = any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant)
                                 and keyword.value.value is True for keyword in node.keywords)
                if sources and (shell_true or tail in {"system", "popen"}):
                    add("command_injection_candidate", "Request-derived data reaches a command execution sink",
                        "A value derived from a request or process input reaches an operating-system command API.",
                        "Avoid shell command construction; pass a fixed executable and separate argument list after strict validation.",
                        "CWE-78", "high", 0.87, node, sources,
                        uncertainty="Static local data flow does not model sanitizers or subprocess wrappers.")
            if tail in _PATH_SINKS and node.args:
                sources = _taint_sources(node.args[0], tainted_names, taint_sources)
                if sources:
                    add("path_traversal_candidate", "Request-derived data reaches a file path operation",
                        "A value derived from a request or process input reaches a file access operation without a statically visible containment check.",
                        "Resolve paths against an allowed root, reject escapes after canonicalization, and authorize access to the resolved target.",
                        "CWE-22", "medium", 0.78, node, sources,
                        uncertainty="Path normalization, containment checks, and authorization may occur outside this local function.")
            if tail in {"mark_safe", "Markup"} and node.args:
                sources = _taint_sources(node.args[0], tainted_names, taint_sources)
                if sources:
                    add("xss_candidate", "Request-derived data is marked safe for HTML output",
                        "A value derived from a request or process input reaches an HTML-safe marking function.",
                        "Apply context-appropriate output encoding and avoid marking untrusted strings as safe.",
                        "CWE-79", "high", 0.85, node, sources,
                        uncertainty="Template autoescaping and any context-specific sanitization were not verified.")
            if any(keyword.arg == "verify" and isinstance(keyword.value, ast.Constant)
                   and keyword.value.value is False for keyword in node.keywords):
                add("tls_verification_disabled", "TLS certificate verification is disabled",
                    "A request call sets verify=False, disabling normal certificate validation for that call.",
                    "Enable certificate verification and configure a trusted CA bundle where needed.",
                    "CWE-295", "high", 0.93, node)

    observations.sort(key=lambda item: (item["lines"][0], item["classification"], item["title"]))
    return {"status": "parsed", "observations": observations}


def inspect_config_secrets(path: str, source: str):
    """Scan selected text configuration files for hardcoded credential-like values."""
    observations = []
    for line_no, line in enumerate(source.splitlines(), 1):
        match = _SECRET_LINE.search(line)
        if not match:
            continue
        value = match.group(3).strip("'\"")
        normalized = value.casefold()
        if len(value) < 12 or normalized in _PLACEHOLDERS or "${" in value:
            continue
        observations.append({"classification": "hardcoded_credential",
            "title": "Credential-shaped value is hardcoded",
            "description": "A credential-like configuration assignment contains a non-placeholder literal. The literal value is intentionally omitted; its validity is unverified.",
            "remediation": "Remove the literal, rotate it if it may have been used, and load credentials from the approved secret store or environment.",
            "cwe_id": "CWE-798", "severity": "high", "confidence": 0.86,
            "lines": [line_no], "uncertainty": "The value's validity and deployment exposure were not verified.",
            "source_lines": []})
    return observations


def _line_node(line_no):
    return ast.Constant(value=None, lineno=line_no, col_offset=0, end_lineno=line_no, end_col_offset=0)


def _scope_nodes(body):
    result, stack = [], list(body)
    while stack:
        node = stack.pop()
        result.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return result


def _call_or_name(node):
    parts = []
    while isinstance(node, (ast.Attribute, ast.Call)):
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        else:
            node = node.func
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _taint_sources(node, tainted_names, taint_sources):
    if node is None:
        return []
    sources = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in tainted_names:
            sources.extend(taint_sources.get(child.id, ()))
        elif isinstance(child, ast.Call) and _call_or_name(child.func).split(".")[-1] == "input":
            sources.append(child)
        elif isinstance(child, ast.Attribute) and child.attr in _SOURCE_ATTRS:
            sources.append(child)
        elif isinstance(child, ast.Subscript) and isinstance(child.value, ast.Attribute) and child.value.attr in _SOURCE_ATTRS:
            sources.append(child)
    unique = {item.lineno: item for item in sources}
    return [unique[line] for line in sorted(unique)]


def _is_dynamic(node):
    if isinstance(node, (ast.JoinedStr, ast.BinOp)):
        return True
    return any(isinstance(child, ast.Call) and
               (_call_or_name(child.func).endswith(".format") or _call_or_name(child.func).endswith(".join"))
               for child in ast.walk(node))


def _has_safe_loader(node):
    for keyword in node.keywords:
        if keyword.arg == "Loader":
            return any(isinstance(item, ast.Name) and item.id == "SafeLoader"
                       for item in ast.walk(keyword.value))
    return False
