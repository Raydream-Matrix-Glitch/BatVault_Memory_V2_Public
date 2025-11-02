import sys, json, base64, argparse
from nacl.signing import VerifyKey
from core_utils.fingerprints import canonical_json, sha256_hex, ensure_sha256_prefix
def load_pubkey(pub_path: str) -> bytes:
    data = open(pub_path, "r", encoding="utf-8").read().strip()
    if data.startswith("-----BEGIN PUBLIC KEY-----"):
        body = "".join(data.splitlines()[1:-1]); spki = base64.b64decode(body); return spki[-32:]
    return base64.b64decode(data)
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("response_json")
    ap.add_argument("receipt_json")
    ap.add_argument("--pubkey", default="public/keys/gateway_ed25519_pub.pem")
    a = ap.parse_args()
    env = json.load(open(a.response_json,"r",encoding="utf-8"))
    resp = env["response"]; meta = resp.get("meta") or {}
    sig = json.load(open(a.receipt_json,"r",encoding="utf-8"))
    if not sig.get("sig"): raise SystemExit("no signature found in receipt.json")
    resp2 = json.loads(json.dumps(resp)); m = resp2.get("meta") or {}; m.pop("bundle_fp", None); resp2["meta"] = m
    covered_hex = sha256_hex(canonical_json(resp2))
    if meta.get("bundle_fp") and meta["bundle_fp"] != ensure_sha256_prefix(covered_hex):
        raise SystemExit(f"bundle_fp mismatch: claimed={meta['bundle_fp']} computed=sha256:{covered_hex}")
    VerifyKey(load_pubkey(a.pubkey)).verify(ensure_sha256_prefix(covered_hex).encode(), base64.b64decode(sig["sig"]))
    print("OK: signature valid; bundle_fp = " + ensure_sha256_prefix(covered_hex))
if __name__ == "__main__": main()
