import pytest, json, difflib


def pytest_assertrepr_compare(op, left, right):
    """Pretty diff for dict-vs-dict comparisons."""
    if isinstance(left, dict) and isinstance(right, dict) and op == "==":
        lhs = json.dumps(left,  indent=2, sort_keys=True).splitlines()
        rhs = json.dumps(right, indent=2, sort_keys=True).splitlines()
        return [""] + list(
            difflib.unified_diff(lhs, rhs, fromfile="left", tofile="right")
        )