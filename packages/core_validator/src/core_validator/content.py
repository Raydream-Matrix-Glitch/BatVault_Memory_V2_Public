import re
from core_utils.ids import slugify_tag
class ValidationError(ValueError): ...

def validate_snippet(val: str) -> None:
    if len(val) > 120:
        raise ValidationError("snippet exceeds 120 chars")

def validate_tags(tags: list[str]) -> list[str]:
    """
    Normalise tag values to the shared slug shape and deduplicate while
    preserving order. Uses core_utils.slugify_tag for consistency across
    services.
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        s = slugify_tag(t)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out