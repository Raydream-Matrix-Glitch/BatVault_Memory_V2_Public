#!/usr/bin/env python3
import sys, json, base64, hashlib, argparse
from nacl.signing import VerifyKey  # pip install pynacl

def canonical_json_bytes(obj) -> bytes:
    # JSON-first canonicalisation: sorted keys, minimal separators, ASCII-safe
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

def normalize_sha256_tag(s: str) -> str:
    # Accept "abcdef..." or "sha256:abcdef..." interchangeably, return bare hex
    return s.split("sha256:", 1)[-1] if s.startswith("sha256:") else s

def load_pubkey(pub_path: str) -> bytes:
    data = open(pub_path, "r", encoding="utf-8").read().strip()
    if data.startswith("-----BEGIN PUBLIC KEY-----"):
        body = "".join(data.splitlines()[1:-1])
        spki = base64.b64decode(body)
        return spki[-32:]  # Ed25519 raw 32B key from SPKI
    # else: assume base64 raw 32B public key
    return base64.b64decode(data)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("envelope", help="envelope from /v2/query (JSON)")
    ap.add_argument("--pubkey", default="public/keys/gateway_ed25519_pub.pem",
                   help="Ed25519 public key (PEM SPKI or base64 raw)")
    args = ap.parse_args()

    env = json.load(open(args.envelope, "r", encoding="utf-8"))
    if env.get("schema_version") != "v3":
        raise SystemExit("unexpected schema_version (expected v3)")

    # The covered content is the canonical JSON of the inner response object,
    # with meta.bundle_fp removed before hashing.
    resp = env["response"]
    meta = resp.get("meta") or {}

    # Signature can be at response.meta.signature or at top-level env.signature
    sig_obj = meta.get("signature") or env.get("signature") or {}
    sig_b64 = sig_obj.get("sig")
    if not sig_b64:
        raise SystemExit("no signature found at response.meta.signature.sig or envelope.signature.sig")
    claimed_covered = sig_obj.get("covered", "")  # e.g. "sha256:<hex>" or "<hex>"
    covered_tagless = normalize_sha256_tag(claimed_covered)

    # Prepare the message: resp WITHOUT meta.bundle_fp
    resp_for_hash = json.loads(json.dumps(resp))  # deep copy via JSON
    m2 = resp_for_hash.get("meta") or {}
    if "bundle_fp" in m2:
        m2.pop("bundle_fp", None)
        resp_for_hash["meta"] = m2

    msg = canonical_json_bytes(resp_for_hash)
    computed_hex = hashlib.sha256(msg).hexdigest()

    # Cross-check: bundle_fp should match the covered hash (ignoring "sha256:" tag)
    bundle_fp = meta.get("bundle_fp")
    if bundle_fp and normalize_sha256_tag(bundle_fp) != computed_hex:
        raise SystemExit(f"bundle_fp mismatch: claimed={bundle_fp} computed=sha256:{computed_hex}")

    # Cross-check: signature.covered should also match
    if covered_tagless and covered_tagless != computed_hex:
        raise SystemExit(f"signature.covered mismatch: claimed={claimed_covered} computed=sha256:{computed_hex}")

    # Verify Ed25519 signature
    vk = VerifyKey(load_pubkey(args.pubkey))
    vk.verify(msg, base64.b64decode(sig_b64))
    print("OK: signature VALID")
    print("bundle_fp       =", f"sha256:{computed_hex}")
    if bundle_fp:
        print("bundle_fp (meta)=", bundle_fp)
    if claimed_covered:
        print("sig.covered     =", claimed_covered)

if __name__ == "__main__":
    main()
