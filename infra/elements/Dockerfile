FROM debian:bookworm-slim

ARG ELEMENTS_VERSION=23.3.0
ARG ELEMENTS_ARCHIVE=elements-23.3.0-x86_64-linux-gnu.tar.gz

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/ElementsProject/elements/releases/download/elements-${ELEMENTS_VERSION}/${ELEMENTS_ARCHIVE}" \
    -o /tmp/elements.tar.gz \
    && mkdir -p /opt/elements \
    && tar -xzf /tmp/elements.tar.gz -C /opt/elements --strip-components=1 \
    && ln -s /opt/elements/bin/elementsd /usr/local/bin/elementsd \
    && ln -s /opt/elements/bin/elements-cli /usr/local/bin/elements-cli \
    && rm /tmp/elements.tar.gz

COPY start-elementsd.sh /usr/local/bin/start-elementsd.sh

RUN chmod +x /usr/local/bin/start-elementsd.sh

ENTRYPOINT ["/usr/local/bin/start-elementsd.sh"]
