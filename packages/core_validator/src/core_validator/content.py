import re
from core_utils.ids import slugify_id
class ValidationError(ValueError): ...

def validate_snippet(val: str) -> None:
    if len(val) > 120:
        raise ValidationError("snippet exceeds 120 chars")

def validate_tags(tags: list[str]) -> list[str]:
    """
    Normalise a list of tag values by converting each entry to a lower‑case
    identifier and replacing any sequence of non‑alphanumeric characters
    with a single underscore.  Leading and trailing underscores are
    removed.  Order is preserved and duplicate values are retained.

    This helper aligns the tag convention with the shared normaliser
    which uses underscores instead of hyphens for slugs.  It performs
    only basic transformation and does not attempt to deduplicate or
    coerce values that are not strings.
    """
    import re
    normalised: list[str] = []
    for tag in tags:
        try:
            s = str(tag).lower()
        except Exception:
            s = f"{tag}".lower()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        s = s.strip("_")
        normalised.append(s)
    return normalised