FROM nginx:1.25-alpine

COPY gateway.conf /etc/nginx/conf.d/gateway.conf
COPY 30-render-cors-map.sh /docker-entrypoint.d/30-render-cors-map.sh
COPY 31-render-cors-snippets.sh /docker-entrypoint.d/31-render-cors-snippets.sh
# Strip any CRLF line endings that may leak in from Windows editors so the
# shebang resolves correctly (kernel would otherwise look for `/bin/sh\r`).
RUN sed -i 's/\r$//' /docker-entrypoint.d/30-render-cors-map.sh \
                     /docker-entrypoint.d/31-render-cors-snippets.sh \
    && chmod +x /docker-entrypoint.d/30-render-cors-map.sh \
                /docker-entrypoint.d/31-render-cors-snippets.sh \
    && mkdir -p /etc/nginx/snippets \
    && rm -f /etc/nginx/conf.d/default.conf

ENV CORS_ALLOWED_ORIGINS=http://localhost:3000 \
    CORS_ALLOWED_METHODS="GET, POST, PUT, PATCH, DELETE, OPTIONS" \
    CORS_ALLOWED_HEADERS="Authorization, Content-Type, X-Requested-With, Accept, Origin, X-Request-ID, X-Correlation-ID, X-API-Key, X-2FA-Code, X-Idempotency-Key" \
    CORS_EXPOSE_HEADERS="X-Request-ID" \
    CORS_MAX_AGE=86400 \
    CORS_ALLOW_CREDENTIALS=true

EXPOSE 8000

CMD ["nginx", "-g", "daemon off;"]
