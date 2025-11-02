from __future__ import annotations
from pathlib import Path
from typing import Optional, Tuple
import os
from core_utils import jsonx
from core_utils.fingerprints import canonical_json, sha256_hex

_CACHE: dict[tuple[Optional[str], str], tuple[dict, str]] = {}

def _config_dir() -> Path:
    # Default search under gateway/config
    return Path(__file__).resolve().parents[2] / "config"

def _candidate_paths(org: Optional[str]) -> list[Path]:
    env_override = os.getenv("GATEWAY_TEMPLATE_REGISTRY_PATH")
    if env_override:
        return [Path(env_override)]
    base = _config_dir()
    out: list[Path] = []
    if org:
        out.append(base / f"answer_templates.{org}.json")
    out.append(base / "answer_templates.json")
    out.append(base / "answer_templates.default.json")
    return out

def load_registry(org: Optional[str]) -> tuple[dict, str]:
    """
    Load the registry JSON for an org (or default). Returns (obj, fingerprint).
    Fingerprint = sha256 over canonical JSON for determinism and to embed in meta.
    """
    key = (org, os.getenv("GATEWAY_TEMPLATE_REGISTRY_PATH", ""))
    if key in _CACHE:
        return _CACHE[key]
    last_err: Exception | None = None
    for p in _candidate_paths(org):
        try:
            with open(p, "rb") as f:
                raw = f.read()
            reg = jsonx.loads(raw)
            fp = f"sha256:{sha256_hex(canonical_json(reg))}"
            _CACHE[key] = (reg, fp)
            return reg, fp
        except FileNotFoundError:
            last_err = None
            continue
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid template registry JSON at {p}") from exc
    tried = ", ".join(map(str, _candidate_paths(org)))
    raise FileNotFoundError(f"no answer template registry found; tried={tried}")

def select_template(template_id: Optional[str], org: Optional[str]) -> tuple[dict, str]:
    """
    Pick a template (by id or registry default) and return (template_obj, registry_fp).
    """
    reg, fp = load_registry(org)
    default_id = str(reg.get("default") or "").strip() or None
    wanted = (template_id or default_id or "").strip()
    if not wanted:
        raise KeyError("no template id provided and no default set")
    for t in (reg.get("templates") or []):
        if isinstance(t, dict) and str(t.get("id") or "") == wanted:
            return t, fp
    raise KeyError(f"template not found: {wanted}")
