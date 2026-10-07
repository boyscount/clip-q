FROM python:3.13-slim

# ffmpeg does the rendering; the Thai font is not optional — without it libass
# draws captions as empty boxes and ffmpeg reports no error at all
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      fonts-noto-core \
      fonts-thai-tlwg \
      fontconfig \
 && fc-cache -f \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30"

COPY src/ ./src/
COPY server/ ./server/
COPY app/ ./app/
COPY tools/ ./tools/

ENV CLIPQUEUE_DB=/data/clipqueue.db \
    CLIPQUEUE_RENDERS=/data/renders \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/api/health').read()"

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8787"]
