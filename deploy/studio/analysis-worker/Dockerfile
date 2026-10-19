# Supply an explicitly selected python:3.13-slim image@sha256 digest at build time.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends jq imagemagick chromium fonts-liberation libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
# Nothing runs while browsing the catalog. Studio overrides this entrypoint,
# supplies its own restricted flags, and never pulls a missing worker image.
USER 65534:65534
ENTRYPOINT ["python3"]
