import sys
from core_config import get_settings

def main() -> int:
    cfg = get_settings()
    dim = int(getattr(cfg, "embedding_dim", 0))
    metric = str(getattr(cfg, "vector_metric", "")).lower()
    ok = dim > 0 and metric in {"cosine", "l2"}
    print(f"[embedding_config] dim={dim} metric={metric} ok={ok}")
    return 0 if ok else 2

if __name__ == "__main__":
    sys.exit(main())
