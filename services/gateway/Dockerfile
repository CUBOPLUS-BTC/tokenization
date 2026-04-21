FROM nginx:1.25-alpine

COPY gateway.conf /etc/nginx/conf.d/gateway.conf
COPY 30-render-cors-map.sh /docker-entrypoint.d/30-render-cors-map.sh
RUN chmod +x /docker-entrypoint.d/30-render-cors-map.sh \
    && rm -f /etc/nginx/conf.d/default.conf

ENV CORS_ALLOWED_ORIGINS=http://localhost:3000

EXPOSE 8000

CMD ["nginx", "-g", "daemon off;"]
