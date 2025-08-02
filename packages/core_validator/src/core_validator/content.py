import re
from core_utils.ids import slugify_id
class ValidationError(ValueError): ...

def validate_snippet(val: str) -> None:
    if len(val) > 120:
        raise ValidationError("snippet exceeds 120 chars")

def validate_tags(tags: list[str]) -> list[str]:
    return [slugify_id(t) for t in tags]