# ── AY Marketing OS — Production Dockerfile ──
FROM python:3.11-slim

# FFmpeg voor video productie
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies eerst (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Applicatiecode
COPY . .

# Runtime directories aanmaken
RUN mkdir -p assets/generated/videos data/campaigns data/brand_memory logs

# Port configuratie (Railway injecteert PORT env var)
ENV PORT=8000
EXPOSE 8000

# Start FastAPI met uvicorn
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}
