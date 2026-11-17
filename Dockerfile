FROM python:3.12-slim
# ffmpeg is invoked as a separate process (never linked); fonts are needed for burned-in captions
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# download the speech model at build time so the first upload is not slow and needs no internet
ENV HF_HOME=/models
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', compute_type='int8')"
COPY . .
# sample videos for the web page and the tests (our own synthetic media, ~30 s to generate)
RUN python -m tools.fixtures fixtures && python -m tools.make_sessions
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/ready')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
