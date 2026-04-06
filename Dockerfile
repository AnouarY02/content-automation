# ── AY Marketing OS — Production Dockerfile ──
FROM python:3.11-slim

# FFmpeg + fonts + Chromium dependencies voor video productie + app recording
# Combineer apt-get calls voor kleinere image layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core fontconfig libfreetype6 \
        # Chromium dependencies (Playwright --with-deps doet dit ook, maar expliciet is betrouwbaarder)
        libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1 \
        libasound2 libatspi2.0-0 libxshmfence1 libxdamage1 libxrandr2 \
        libxcomposite1 libxfixes3 libcups2 libpango-1.0-0 libcairo2 && \
    fc-cache -f && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies eerst (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium browser voor app screen recording
# NIET --with-deps gebruiken: die probeert ttf-unifont en ttf-ubuntu-font-family
# te installeren die niet bestaan in python:3.11-slim. Alle benodigde libs staan
# al hierboven geïnstalleerd.
RUN playwright install chromium && \
    echo "Chromium installed at: $(python -c 'from playwright._impl._driver import compute_driver_executable; print(compute_driver_executable())'  2>/dev/null || echo 'unknown')"

# Applicatiecode
COPY . .

# Runtime directories aanmaken
RUN mkdir -p assets/generated/videos data/campaigns data/brand_memory logs

# FFmpeg: 1 thread om RAM te besparen op Railway (512 MB container)
ENV FFMPEG_THREADS=1
# Playwright uit — bespaart 200-400 MB RAM (Chromium browser)
ENV SKIP_PLAYWRIGHT=true
# Python memory: geef RAM direct terug aan OS na grote allocaties
ENV MALLOC_TRIM_THRESHOLD_=65536
ENV PYTHONUNBUFFERED=1

# Port configuratie (Railway injecteert PORT env var)
ENV PORT=8000
EXPOSE 8000

# Healthcheck (Railway + monitoring)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Start FastAPI met uvicorn
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 120
