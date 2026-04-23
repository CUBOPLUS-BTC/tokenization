#!/bin/sh
set -eu
# Render CORS snippet files from env vars so gateway.conf can include them
# instead of duplicating Access-Control-* headers on every location block.
#
# Output:
#   /etc/nginx/snippets/cors.conf           <- unified snippet used by gateway.conf
#   /etc/nginx/snippets/cors-preflight.conf <- legacy, kept for backwards-compat
#   /etc/nginx/snippets/cors-headers.conf   <- legacy, kept for backwards-compat

CORS_ALLOWED_METHODS="${CORS_ALLOWED_METHODS:-GET, POST, PUT, PATCH, DELETE, OPTIONS}"
CORS_ALLOWED_HEADERS="${CORS_ALLOWED_HEADERS:-Authorization, Content-Type, X-Requested-With, Accept, Origin, X-Request-ID, X-Correlation-ID, X-API-Key, X-2FA-Code, X-Idempotency-Key}"
CORS_EXPOSE_HEADERS="${CORS_EXPOSE_HEADERS:-X-Request-ID}"
CORS_MAX_AGE="${CORS_MAX_AGE:-86400}"
CORS_ALLOW_CREDENTIALS="${CORS_ALLOW_CREDENTIALS:-true}"

snippets_dir=/etc/nginx/snippets
mkdir -p "$snippets_dir"

# -----------------------------------------------------------------------------
# Unified snippet: handles preflight (OPTIONS -> 204) and adds Access-Control-*
# headers to every other response (including 4xx/5xx thanks to `always`).
# This is the ONLY snippet gateway.conf should include going forward.
# -----------------------------------------------------------------------------
cat > "$snippets_dir/cors.conf" <<EOF
if (\$request_method = OPTIONS) {
    add_header Access-Control-Allow-Origin      \$cors_origin always;
    add_header Access-Control-Allow-Methods     "${CORS_ALLOWED_METHODS}" always;
    add_header Access-Control-Allow-Headers     "${CORS_ALLOWED_HEADERS}" always;
    add_header Access-Control-Allow-Credentials "${CORS_ALLOW_CREDENTIALS}" always;
    add_header Access-Control-Max-Age           ${CORS_MAX_AGE} always;
    add_header Vary                             "Origin" always;
    add_header Content-Length 0;
    add_header Content-Type "text/plain; charset=UTF-8";
    return 204;
}
add_header Access-Control-Allow-Origin      \$cors_origin always;
add_header Access-Control-Allow-Credentials "${CORS_ALLOW_CREDENTIALS}" always;
add_header Access-Control-Expose-Headers    "${CORS_EXPOSE_HEADERS}" always;
add_header Vary                             "Origin" always;
EOF

# -----------------------------------------------------------------------------
# Legacy snippets (kept for backwards-compat with any external includes).
# New code should include cors.conf instead.
# -----------------------------------------------------------------------------
cat > "$snippets_dir/cors-preflight.conf" <<EOF
if (\$request_method = OPTIONS) {
    add_header Access-Control-Allow-Origin      \$cors_origin always;
    add_header Access-Control-Allow-Methods     "${CORS_ALLOWED_METHODS}" always;
    add_header Access-Control-Allow-Headers     "${CORS_ALLOWED_HEADERS}" always;
    add_header Access-Control-Allow-Credentials "${CORS_ALLOW_CREDENTIALS}" always;
    add_header Access-Control-Max-Age           ${CORS_MAX_AGE} always;
    add_header Vary                             "Origin" always;
    add_header Content-Length 0;
    add_header Content-Type "text/plain; charset=UTF-8";
    return 204;
}
EOF

cat > "$snippets_dir/cors-headers.conf" <<EOF
add_header Access-Control-Allow-Origin      \$cors_origin always;
add_header Access-Control-Allow-Credentials "${CORS_ALLOW_CREDENTIALS}" always;
add_header Access-Control-Expose-Headers    "${CORS_EXPOSE_HEADERS}" always;
add_header Vary                             "Origin" always;
EOF
