"""Revolut Business bağlantısının tek seferlik kurulumu (GmbH).

1) Anahtar ve sertifika üret:
    python scripts/revolut_setup.py keys
   revolut-keys/ altına private.pem (gizli, repoya girmez) ve publiccert.cer yazar.

2) Revolut Business web → Settings → APIs → Business API → Add API certificate:
   - Title: Pusula
   - OAuth redirect URI: https://rexven-pusula.netlify.app
   - X509 public key: publiccert.cer içeriği
   Kaydedince çıkan Client ID'yi kopyala, "Enable access" ile onay ver.
   Tarayıcı https://rexven-pusula.netlify.app/?code=oa_prod_... adresine döner;
   code 2 dakika geçerli.

3) Kodu token'a çevir, hesapları ekle, secret'ları yaz:
    python scripts/revolut_setup.py token --client-id <ID> --code oa_prod_...
   Refresh token ekrana basılmaz; GitHub Actions secret'larına gh ile yazılır,
   GmbH'nin Revolut hesapları fin_accounts'a eklenir (var olan atlanır).
   Vercel'e aynı dört değer elle eklenir (panelde "Hesapları güncelle" için).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

from pusula.finance import store
from pusula.finance.providers.base import request_json
from pusula.finance.providers.revolut import BASE, client_assertion
from pusula.panel_data import connect

KEY_DIR = Path("revolut-keys")
ISS = "rexven-pusula.netlify.app"
COMPANY = "gmbh"
CRED = "GMBH"


def make_keys(out: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    out.mkdir(exist_ok=True)
    private = out / "private.pem"
    if private.exists():
        sys.exit(f"{private} zaten var; yeniden üretmek için önce sil")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Pusula Rexven GmbH")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1825))
        .sign(key, hashes.SHA256())
    )
    (out / "publiccert.cer").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"yazıldı: {private} (gizli), {out / 'publiccert.cer'} (Revolut'a yapıştır)")


def set_secret(name: str, value: str) -> None:
    subprocess.run(["gh", "secret", "set", name], input=value.encode("utf-8"), check=True)
    print(f"secret yazıldı: {name}")


def exchange(client_id: str, code: str, pem: str) -> str:
    with httpx.Client(timeout=30) as client:
        data = request_json(
            client,
            "Revolut",
            "POST",
            f"{BASE}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": client_assertion(client_id, ISS, pem),
            },
        )
        refresh = data.get("refresh_token") if isinstance(data, dict) else None
        if not refresh:
            sys.exit("Revolut refresh_token dönmedi; kod süresi dolmuş olabilir, onayı tekrarla")
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        accounts = request_json(client, "Revolut", "GET", f"{BASE}/accounts", headers=headers)
    add_accounts([a for a in accounts or [] if str(a.get("state")).lower() == "active"])
    return str(refresh)


def add_accounts(accounts: list[dict[str, object]]) -> None:
    with connect() as conn:
        known = {a.provider_account_id for a in store.load_accounts(conn) if a.provider == "revolut"}
        for acc in accounts:
            if acc["id"] in known:
                print(f"zaten var: Revolut {acc['currency']}")
                continue
            store.insert_row(
                conn,
                "fin_accounts",
                {
                    "company_id": COMPANY,
                    "name": f"Revolut {acc.get('name') or acc['currency']}",
                    "institution": "Revolut",
                    "type": "fintech",
                    "currency": str(acc["currency"]).upper(),
                    "provider": "revolut",
                    "provider_account_id": str(acc["id"]),
                    "credential_key": CRED,
                },
            )
            print(f"eklendi: Revolut {acc['currency']}")
        conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Revolut Business kurulumu (GmbH).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    keys = sub.add_parser("keys", help="Anahtar + sertifika üret")
    keys.add_argument("--out", type=Path, default=KEY_DIR)
    token = sub.add_parser("token", help="Onay kodunu refresh token'a çevir")
    token.add_argument("--client-id", required=True)
    token.add_argument("--code", required=True)
    token.add_argument("--key", type=Path, default=KEY_DIR / "private.pem")
    args = parser.parse_args()

    load_dotenv()
    if args.cmd == "keys":
        make_keys(args.out)
        return 0
    pem = args.key.read_text(encoding="utf-8")
    refresh = exchange(args.client_id, args.code.strip(), pem)
    set_secret(f"REVOLUT_CLIENT_ID_{CRED}", args.client_id)
    set_secret(f"REVOLUT_PRIVATE_KEY_{CRED}", pem)
    set_secret(f"REVOLUT_REFRESH_TOKEN_{CRED}", refresh)
    set_secret(f"REVOLUT_ISS_{CRED}", ISS)
    print("tamam: GitHub secret'ları yazıldı. Vercel'e aynı dört değeri ekleyip redeploy et.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
