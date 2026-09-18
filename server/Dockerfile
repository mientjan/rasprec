# Multi-arch: docker buildx build --platform linux/amd64,linux/arm64 .
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nvr/ /app/nvr/

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# tini reaps the ffmpeg children the motion detector spawns and restarts.
ENTRYPOINT ["/usr/bin/tini", "--", "python"]
CMD ["-m", "nvr"]
