"""
Static analysis: verifies that every mandatory pipeline stage is wrapped in
`trace_span` (either decorator or `trace_span.ctx()` context-manager call).
"""

import ast, importlib, inspect

_REQUIRED = {
    "resolve",
    "plan",
    "exec",
    "enrich",
    "bundle",
    "prompt",
    "llm",
    "validate",
    "render",
    "stream",
}


def _spans_in_module(module_name: str) -> set[str]:
    src = inspect.getsource(importlib.import_module(module_name))
    tree = ast.parse(src)
    names: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):  # trace_span(...) or trace_span.ctx(...)
            if isinstance(node.func, ast.Name) and node.func.id == "trace_span":
                if node.args and isinstance(node.args[0], ast.Constant):
                    names.add(node.args[0].value)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "ctx":
                if node.args and isinstance(node.args[0], ast.Constant):
                    names.add(node.args[0].value)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return names


def test_all_required_stages_have_spans() -> None:
    found = set()
    for mod in (
        "gateway.app",
        "gateway.evidence",
        "gateway.prompt_envelope",
    ):
        found |= _spans_in_module(mod)

    missing = _REQUIRED - found
    assert not missing, f"Missing trace_span instrumentation for: {sorted(missing)}"