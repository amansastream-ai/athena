#!/usr/bin/env bash
# Tests de fumee post-deploiement.
set -euo pipefail
BASE="${1:-http://localhost:8000}"
fail=0
check() {
  local path="$1" expected="$2"
  code=$(curl -s -o /tmp/jaay-smoke-body -w '%{http_code}' "$BASE$path" || true)
  if [[ "$code" == "$expected" ]]; then echo "OK  $path -> $code"; else echo "KO  $path -> $code (attendu $expected)"; fail=1; fi
}
check /health 200
check / 200
check /catalogue 200
check /connexion 200
check /pages/commission 200
check /api/payments/capabilities 200
check /api/categories 200
check /api/docs 200
check /admin 303
exit $fail
