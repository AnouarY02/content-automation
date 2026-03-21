# ── AY Marketing OS — Production Dockerfile ──
FROM python:3.11-slim

# FFmpeg + fonts voor video productie (drawtext vereist freetype + fontconfig)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fontconfig libfreetype6 && \
    fc-cache -f && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies eerst (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Applicatiecode
COPY . .

# Runtime directories aanmaken
RUN mkdir -p assets/generated/videos data/campaigns data/brand_memory logs

# FFmpeg memory limiet — voorkom dat encoding alle RAM claimt
ENV FFMPEG_THREADS=2

# Port configuratie (Railway injecteert PORT env var)
ENV PORT=8000
EXPOSE 8000

# Healthcheck (Railway + monitoring)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Start FastAPI met uvicorn
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}
