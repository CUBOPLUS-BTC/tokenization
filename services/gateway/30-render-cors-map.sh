#!/bin/sh
set -eu
# Build nginx map from comma-separated CORS_ALLOWED_ORIGINS (trim spaces per entry).
CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-http://localhost:3000}"
# If empty or whitespace-only, fallback so $cors_origin is always defined in the map.
if [ -z "$(printf %s "$CORS_ALLOWED_ORIGINS" | tr -d '[:space:],')" ]; then
  CORS_ALLOWED_ORIGINS="http://localhost:3000"
fi
out=/etc/nginx/conf.d/cors_map.conf
{
  echo "map \$http_origin \$cors_origin {"
  echo "    default \"\";"
  IFS=','
  for o in $CORS_ALLOWED_ORIGINS; do
    trimmed=$(echo "$o" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    [ -z "$trimmed" ] || echo "    \"$trimmed\" \$http_origin;"
  done
  echo "}"
} > "$out"
