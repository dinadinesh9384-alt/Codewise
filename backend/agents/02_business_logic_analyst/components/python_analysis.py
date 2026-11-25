"""Non executing Python AST inventory for business relevant source structure."""

import ast


def inspect_python(source: str):
    """Return JSON friendly facts; malformed Python is reported without importing it."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return {"status": "malformed", "error": type(exc).__name__, "symbols": [], "imports": [], "calls": [],
                "conditions": [], "models": [], "routes": []}
    symbols, imports, calls, conditions, models, routes = [], [], [], [], [], []
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parent = parents.get(node)
            if isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            symbols.append({"name": node.name, "kind": kind, "start_line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "decorators": [ast.unparse(item)[:160] for item in node.decorator_list]})
            if kind == "class":
                base_text = " ".join(ast.unparse(item) for item in node.bases)
                fields = []
                for child in node.body:
                    if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                        fields.append({"name": child.target.id, "type": ast.unparse(child.annotation)[:160],
                                       "line": child.lineno})
                    elif isinstance(child, ast.Assign):
                        field_type = ast.unparse(child.value.func) if isinstance(child.value, ast.Call) else ast.unparse(child.value)
                        for target in child.targets:
                            if isinstance(target, ast.Name) and isinstance(child.value, ast.Call):
                                fields.append({"name": target.id, "type": field_type[:160], "line": child.lineno,
                                               "target": ast.unparse(child.value.args[0])[:160] if child.value.args else None})
                if any(token in (node.name + " " + base_text).casefold() for token in
                       ("model", "entity", "schema", "record", "document")) or fields:
                    models.append({"name": node.name, "bases": base_text[:300], "fields": fields[:80],
                                   "start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})
        elif isinstance(node, ast.Import):
            imports.extend({"module": item.name, "line": node.lineno} for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append({"module": "." * node.level + (node.module or ""), "line": node.lineno})
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name:
                calls.append({"name": name, "line": node.lineno})
                if name.rsplit(".", 1)[-1].casefold() in {"path", "route", "url", "re_path"}:
                    routes.append({"call": name, "line": node.lineno, "expression": ast.get_source_segment(source, node)[:300]})
        elif isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert, ast.Match)):
            expression = node.test if hasattr(node, "test") else node.subject if isinstance(node, ast.Match) else None
            if expression is not None:
                conditions.append({"expression": ast.unparse(expression)[:400], "line": node.lineno,
                                   "kind": type(node).__name__})
    return {"status": "parsed", "error": None, "symbols": sorted(symbols, key=lambda x: (x["start_line"], x["name"])),
            "imports": sorted(imports, key=lambda x: (x["line"], x["module"])),
            "calls": sorted(calls, key=lambda x: (x["line"], x["name"])),
            "conditions": sorted(conditions, key=lambda x: (x["line"], x["expression"])),
            "models": sorted(models, key=lambda x: (x["start_line"], x["name"])),
            "routes": sorted(routes, key=lambda x: (x["line"], x["call"]))}


def _dotted(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""
