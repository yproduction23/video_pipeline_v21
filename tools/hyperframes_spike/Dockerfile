# HyperFrames 시험용 렌더 컨테이너(2026-10-19 운영자 요청: 그래픽 렌더러 시험). 운영 경로에는 아직 연결하지 않는다.
FROM node:22-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg chromium fonts-noto-cjk fonts-nanum ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
ENV PUPPETEER_SKIP_DOWNLOAD=1 PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium CHROME_PATH=/usr/bin/chromium
WORKDIR /work
