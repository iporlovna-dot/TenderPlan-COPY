"""Собрать CA-бандл (certifi + Russian Trusted CA Минцифры) для скачивания ТЗ с ЕИС.

Зачем: файлы ТЗ лежат на zakupki.gov.ru, чей TLS-сертификат выдан национальным УЦ
Минцифры («Russian Trusted Sub CA»), которого нет в доверенных у Python, а сам сервер
промежуточный сертификат не досылает. Правильное решение (без отключения проверки) —
добавить в доверенный бандл корневой сертификат УЦ и нужные промежуточные. Проверка
сертификата ОСТАЁТСЯ включённой.

Модель доверия:
  • КОРЕНЬ — единственный якорь доверия. Качаем с офиц. источника Госуслуг (gu-st.ru,
    глобально доверенный TLS) и сверяем с ЗАШИТЫМ здесь SHA-256 отпечатком (пиннинг).
  • ПРОМЕЖУТОЧНЫЕ (sub-CA) — качаем с офиц. CDP Минцифры и КРИПТОГРАФИЧЕСКИ проверяем,
    что каждый подписан пиннингованным корнем. Поэтому загрузка по http безопасна и
    список переживает ротацию sub-CA (2024/2025/...): подделка не пройдёт проверку подписи.

Запуск:  .venv/bin/python scripts/setup_ca_bundle.py
Результат: certs/ru_trusted_bundle.pem — адаптер подхватит автоматически.
"""
import hashlib
import os
import ssl
import sys

import certifi
import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding

ROOT_URL = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
ROOT_SHA256 = "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31"

# Промежуточные sub-CA из официальных CDP Минцифры (подпись сверяем против корня).
SUB_URLS = [
    "http://nuc-cdp.digital.gov.ru/cdp/subca_ssl_rsa2024.crt",  # им подписан zakupki.gov.ru
]

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "certs", "ru_trusted_bundle.pem"))


def _load_cert(data: bytes) -> x509.Certificate:
    """Разобрать сертификат в любом формате (PEM или DER)."""
    try:
        return x509.load_pem_x509_certificate(data)
    except ValueError:
        return x509.load_der_x509_certificate(data)


def _signed_by(child: x509.Certificate, issuer: x509.Certificate) -> bool:
    """Криптопроверка: RSA-подпись child сделана ключом issuer."""
    try:
        issuer.public_key().verify(
            child.signature, child.tbs_certificate_bytes,
            padding.PKCS1v15(), child.signature_hash_algorithm,
        )
        return True
    except InvalidSignature:
        return False


def main():
    # 1) Корень — по проверенному TLS + пиннинг SHA-256.
    print("Корень Russian Trusted Root CA (пиннинг SHA-256):")
    r = httpx.get(ROOT_URL, timeout=30)
    r.raise_for_status()
    root_pem = r.text.strip() + "\n"
    got = hashlib.sha256(ssl.PEM_cert_to_DER_cert(root_pem)).hexdigest().upper()
    if got != ROOT_SHA256:
        sys.exit("ОТПЕЧАТОК КОРНЯ НЕ СОВПАЛ:\n  получено:  %s\n  ожидалось: %s" % (got, ROOT_SHA256))
    print("  OK sha256=%s…" % got[:16])
    root = _load_cert(root_pem.encode())

    # 2) Промежуточные — из CDP + проверка подписи корнем.
    print("Промежуточные sub-CA (проверка подписи корнем):")
    sub_pems = []
    for url in SUB_URLS:
        data = httpx.get(url, timeout=30).content
        sub = _load_cert(data)
        if not _signed_by(sub, root):
            sys.exit("  ПОДПИСЬ НЕ ПРОШЛА для %s — sub не подписан пиннингованным корнем" % url)
        sub_pems.append(sub.public_bytes(Encoding.PEM).decode())
        print("  OK %s" % url)

    # 3) Бандл: certifi + корень + проверенные промежуточные.
    with open(certifi.where(), encoding="utf-8") as f:
        base = f.read()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(base.rstrip() + "\n\n")
        f.write("# --- Russian Trusted CA (Минцифры), setup_ca_bundle.py ---\n")
        f.write(root_pem)
        f.write("".join(sub_pems))

    print("\nБандл собран: %s" % OUT)
    print("Проверка сертификатов ВКЛючена; адаптер подхватит бандл автоматически.")


if __name__ == "__main__":
    main()
