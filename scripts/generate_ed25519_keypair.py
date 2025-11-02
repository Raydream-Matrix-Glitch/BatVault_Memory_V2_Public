#!/usr/bin/env python3
import base64
from nacl import signing


def main() -> None:
    sk = signing.SigningKey.generate()
    pk = sk.verify_key

    # 32-byte seed (private) → base64
    priv_b64 = base64.b64encode(bytes(sk._seed)).decode("ascii")
    # 32-byte public → base64
    pub_b64 = base64.b64encode(bytes(pk)).decode("ascii")

    print("# BatVault / Gateway Ed25519 keypair")
    print(f"GATEWAY_ED25519_PRIV_B64={priv_b64}")
    print(f"GATEWAY_ED25519_PUB_B64={pub_b64}")


if __name__ == "__main__":
    main()
