from jsonschema import Draft202012Validator as V
from ingest.cli import load_schema


def _fixture_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures directory not found")

ROOT = _fixture_root()

def _infer_schema(doc: dict) -> str:
    if {"from", "to"} <= doc.keys():   # transition
        return "transition"
    if "option" in doc:                # decision
        return "decision"
    return "event"

@pytest.mark.skipif(not ROOT.exists(), reason="memory/fixtures missing")
@pytest.mark.parametrize("p", ROOT.rglob("*.json"))
def test_fixture_passes_schema(p: pathlib.Path):
    data = json.loads(p.read_text())
    schema = load_schema(_infer_schema(data))
    V(schema).validate(data)
