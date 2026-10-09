FROM node:24-bookworm-slim AS javascript
FROM python:3.12-slim-bookworm
COPY --from=javascript /usr/local/bin/node /usr/local/bin/node
# Optional build-time CA bundle supports managed TLS proxies without disabling verification.
RUN --mount=type=secret,id=build_ca,mode=0444 \
    set -eu; ca_file=/etc/ssl/certs/ca-certificates.crt; \
    if [ -f /run/secrets/build_ca ]; then ca_file=/run/secrets/build_ca; fi; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    apt-get -o Acquire::https::CaInfo="$ca_file" update -o APT::Update::Error-Mode=any; \
    apt-get -o Acquire::https::CaInfo="$ca_file" install -y --no-install-recommends ffmpeg; \
    rm -rf /var/lib/apt/lists/*
WORKDIR /opt/streamvault
COPY requirements.lock .
RUN --mount=type=secret,id=build_ca,mode=0444 \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m pip install --no-cache-dir -r requirements.lock
COPY app ./app
RUN useradd --create-home streamvault \
    && chmod -R a+rX /opt/streamvault/app \
    && mkdir /data && chown streamvault:streamvault /data
USER streamvault
ENV DOWNLOAD_DIR=/data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
