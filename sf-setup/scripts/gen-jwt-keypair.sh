#!/usr/bin/env bash
#
# gen-jwt-keypair.sh — generate ONE JWT keypair reused across every org.
#
# Produces (in the output dir, default ./.secrets):
#   server.key      private key (PEM)          -> becomes SF_JWT_KEY (base64)
#   server.crt      public certificate (PEM)   -> uploaded into the Connected App
#   server.key.b64  single-line base64 of key  -> the exact SF_JWT_KEY secret value
#   cert.body       cert base64 body (no PEM headers) -> injected into connectedApp
#
# Usage: gen-jwt-keypair.sh [--cn <common-name>] [--out <dir>] [--days <n>]
set -euo pipefail

CN="sf-cicd"
OUT=".secrets"
DAYS=3650

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cn)   CN="$2";   shift 2 ;;
    --out)  OUT="$2";  shift 2 ;;
    --days) DAYS="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "$OUT"

if [[ -f "$OUT/server.key" ]]; then
  echo "server.key already exists in $OUT — reusing it (delete it to regenerate)." >&2
else
  openssl req -x509 -sha256 -nodes -days "$DAYS" -newkey rsa:2048 \
    -keyout "$OUT/server.key" -out "$OUT/server.crt" \
    -subj "/CN=$CN"
fi

# Single-line base64 of the private key (portable across macOS/Linux).
base64 < "$OUT/server.key" | tr -d '\n' > "$OUT/server.key.b64"

# Certificate body only (strip PEM header/footer + newlines) for the metadata field.
grep -v -- '-----' "$OUT/server.crt" | tr -d '\n' > "$OUT/cert.body"

chmod 600 "$OUT/server.key" "$OUT/server.key.b64"

echo "Wrote keypair to $OUT/ :"
echo "  server.key      (KEEP SECRET)"
echo "  server.crt      (public cert)"
echo "  server.key.b64  (= SF_JWT_KEY secret value)"
echo "  cert.body       (injected into the Connected App by bootstrap-org.sh)"
