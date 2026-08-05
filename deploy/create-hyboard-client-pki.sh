#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 CA_PASS_FILE P12_PASS_FILE OUTPUT_DIR" >&2
  exit 2
fi

CA_PASS_FILE=$1
P12_PASS_FILE=$2
OUTPUT_DIR=$3

test -s "$CA_PASS_FILE"
test -s "$P12_PASS_FILE"
case "$OUTPUT_DIR" in
  /root/hyboard-client-pki-*) ;;
  *)
    echo "Refusing unexpected PKI output path" >&2
    exit 2
    ;;
esac

umask 077
install -d -m 0700 "$OUTPUT_DIR"

openssl genpkey \
  -algorithm EC \
  -pkeyopt ec_paramgen_curve:P-256 \
  -aes-256-cbc \
  -pass "file:$CA_PASS_FILE" \
  -out "$OUTPUT_DIR/client-ca.key"

openssl req \
  -x509 \
  -new \
  -sha256 \
  -days 3650 \
  -subj '/CN=HyBoard Client CA' \
  -key "$OUTPUT_DIR/client-ca.key" \
  -passin "file:$CA_PASS_FILE" \
  -out "$OUTPUT_DIR/client-ca.crt"

openssl genpkey \
  -algorithm EC \
  -pkeyopt ec_paramgen_curve:P-256 \
  -out "$OUTPUT_DIR/android-primary.key"

openssl req \
  -new \
  -sha256 \
  -subj '/CN=HyBoard Android Primary' \
  -key "$OUTPUT_DIR/android-primary.key" \
  -out "$OUTPUT_DIR/android-primary.csr"

printf '%s\n' \
  'basicConstraints=critical,CA:FALSE' \
  'keyUsage=critical,digitalSignature' \
  'extendedKeyUsage=critical,clientAuth' \
  'subjectKeyIdentifier=hash' \
  'authorityKeyIdentifier=keyid,issuer' \
  > "$OUTPUT_DIR/android-primary.ext"

openssl x509 \
  -req \
  -sha256 \
  -days 825 \
  -in "$OUTPUT_DIR/android-primary.csr" \
  -CA "$OUTPUT_DIR/client-ca.crt" \
  -CAkey "$OUTPUT_DIR/client-ca.key" \
  -passin "file:$CA_PASS_FILE" \
  -CAcreateserial \
  -extfile "$OUTPUT_DIR/android-primary.ext" \
  -out "$OUTPUT_DIR/android-primary.crt"

openssl pkcs12 \
  -export \
  -name 'HyBoard Android Primary' \
  -inkey "$OUTPUT_DIR/android-primary.key" \
  -in "$OUTPUT_DIR/android-primary.crt" \
  -certfile "$OUTPUT_DIR/client-ca.crt" \
  -passout "file:$P12_PASS_FILE" \
  -out "$OUTPUT_DIR/hyboard-android-primary.p12"

install -o root -g root -m 0644 \
  "$OUTPUT_DIR/client-ca.crt" \
  /etc/nginx/hyboard-client-ca.crt

openssl verify \
  -purpose sslclient \
  -CAfile "$OUTPUT_DIR/client-ca.crt" \
  "$OUTPUT_DIR/android-primary.crt"

