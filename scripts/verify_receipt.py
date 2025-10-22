#!/usr/bin/env python3
import sys, json, base64, hashlib, argparse
from nacl.signing import VerifyKey
def canonical_json(obj)->bytes:
    return json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True).encode()
def load_pubkey(pub_path: str) -> bytes:
    data = open(pub_path, "r", encoding="utf-8").read().strip()
    if data.startswith("-----BEGIN PUBLIC KEY-----"):
        body = "".join(data.splitlines()[1:-1]); spki = base64.b64decode(body); return spki[-32:]
    return base64.b64decode(data)
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("envelope"); ap.add_argument("--pubkey", default="public/keys/gateway_ed25519_pub.pem"); a = ap.parse_args()
    env = json.load(open(a.envelope,"r",encoding="utf-8")); resp = env["response"]; meta = resp.get("meta") or {}
    sig = (meta.get("signature") or env.get("signature") or {}); sig_b64 = sig.get("sig") or sig.get("signature") or ""
    if not sig_b64: raise SystemExit("no signature found (meta.signature.sig)")
    resp2 = json.loads(json.dumps(resp)); m = resp2.get("meta") or {}; m.pop("bundle_fp", None); resp2["meta"] = m
    covered = hashlib.sha256(canonical_json(resp2)).hexdigest()
    if meta.get("bundle_fp") and meta["bundle_fp"] != covered: raise SystemExit(f"bundle_fp mismatch: claimed={meta['bundle_fp']} computed={covered}")
    VerifyKey(load_pubkey(a.pubkey)).verify(canonical_json(resp2), base64.b64decode(sig_b64))
    print("OK: signature valid; bundle_fp =", covered)
if __name__ == "__main__": main()
