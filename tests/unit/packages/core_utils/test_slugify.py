from core_utils import slugify_id
import re

RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$")

def test_slugify_canonicalizes():
    s = slugify_id("  Pause PaaS Rollout 2024/Q3  ")
    assert s == "pause-paas-rollout-2024-q3"
    assert RE.match(s)

def test_slugify_collapse_hyphens():
    s = slugify_id("a---b___c")
    assert s == "a-b-c"
