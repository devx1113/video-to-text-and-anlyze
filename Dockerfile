FROM python:3.11-slim

# ffmpeg for extracting audio from video. Installing it system-wide makes the
# image self-contained; app.py prefers this binary over the imageio-ffmpeg one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code only (NOT .env — secrets are passed at runtime via --env-file).
COPY app.py .
COPY static/ static/

EXPOSE 8000

# Bind to 0.0.0.0 (not 127.0.0.1) so the published port is reachable from the host.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
