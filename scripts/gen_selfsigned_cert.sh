#!/usr/bin/env bash
# Идемпотентная генерация самоподписанного сертификата для ВМ vm-re.simbirsoft.
#
# Область записи: только каталог TLS (по умолчанию /opt/re_web_presale/tls).
# Без флага существующий сертификат НЕ затирается. --force — пересоздать.
# Секреты (ключ) не печатаются; выводится только отпечаток SHA256.
#
# Запуск на ВМ:  bash scripts/gen_selfsigned_cert.sh
#                 bash scripts/gen_selfsigned_cert.sh --force
set -euo pipefail

TLS_DIR="${TLS_DIR:-/opt/re_web_presale/tls}"
CN="${CERT_CN:-vm-re.simbirsoft}"
DAYS="${CERT_DAYS:-825}"
FORCE="${1:-}"

if [ "$FORCE" != "--force" ] && [ -f "$TLS_DIR/server.key" ] && [ -f "$TLS_DIR/server.crt" ]; then
  echo "CERT_EXISTS (не перезаписан): $TLS_DIR/server.crt"
else
  mkdir -p "$TLS_DIR"
  openssl req -x509 -nodes -newkey rsa:2048 -sha256 \
    -days "$DAYS" \
    -keyout "$TLS_DIR/server.key" \
    -out "$TLS_DIR/server.crt" \
    -subj "/C=RU/ST=Samara/L=Samara/O=Simbirsoft/OU=RE_web_presale/CN=$CN" \
    -addext "subjectAltName=DNS:vm-re.simbirsoft,DNS:vm-re,IP:10.73.11.110" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth"
  echo "CERT_GENERATED: $TLS_DIR/server.crt"
fi

chmod 700 "$TLS_DIR"
chmod 600 "$TLS_DIR/server.key"
chmod 644 "$TLS_DIR/server.crt"
ls -l "$TLS_DIR"

echo "=== Отпечаток SHA256 ==="
openssl x509 -in "$TLS_DIR/server.crt" -noout -fingerprint -sha256
