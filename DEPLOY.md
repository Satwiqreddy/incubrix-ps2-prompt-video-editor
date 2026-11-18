# Deploying the live demo

The demo is one Docker container (API + web page). It needs **2 GB RAM or more** (the speech model uses about 1 GB), a **10 GB disk** and Docker.
Samples and the Whisper model are built into the image, so a reviewer sees a working page right away.

## Current deployment (what is actually running)
* Google Cloud Compute Engine VM `demo`, region asia-south1-c, e2-medium (2 vCPU, 4 GB), Ubuntu 22.04, public address http://8.231.91.61/
* Code copied from a clean clone, Python 3.10 virtualenv, ffmpeg from apt, Whisper `base.en` pre-downloaded.
* Started with: `sudo HF_HOME=/home/<user>/.cache/huggingface PUBLIC_URL=http://<ip> nohup venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 80`
* No access token (demo is open); `RATE_LIMIT_PER_MIN` defaults to 120 per IP. Paid for from the Google Cloud free-trial credit.
* After a VM reboot the app must be started again with the command above.

## 1. A small Linux server (recommended)
```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/Satwiqreddy/incubrix-ps2-prompt-video-editor.git app && cd app
docker build -t cutting-room .
docker run -d --name demo --restart unless-stopped -p 8000:8000 -v demo-data:/data \
  -e PUBLIC_URL=https://YOUR-DOMAIN-OR-IP \
  -e API_TOKEN=CHOOSE-A-LONG-RANDOM-TOKEN \
  -e RATE_LIMIT_PER_MIN=60 \
  -e CALLBACK_SECRET=ANOTHER-RANDOM-STRING \
  cutting-room
curl http://localhost:8000/api/v1/ready          # expect {"status":"ready",...}
```
`PUBLIC_URL` must be the address reviewers use; download links are built from it. The container uses local disk for outputs (`STORAGE=local`), so MinIO is not needed for the demo.

HTTPS with a domain pointed at the server:
```bash
docker run -d --name caddy --restart unless-stopped -p 80:80 -p 443:443 -v caddy-data:/data \
  --add-host=host.docker.internal:host-gateway caddy caddy reverse-proxy --from YOUR-DOMAIN --to host.docker.internal:8000
```

## 2. Fly.io / Railway
Deploy from the Dockerfile, choose a 2 GB machine, mount a volume at `/data`, set the same environment variables.

## 3. Full stack with MinIO (local or VM)
`docker compose up` starts the API on :8000 and MinIO on :9000/:9001. All tests with no API keys: `docker compose run --rm tests`.

## What to tell the reviewers
* URL, and the access token (paste it under **Options** on the page; the API takes `Authorization: Bearer <token>`).
* Cold start: none (model is in the image). First transcription of an uploaded video without a transcript file takes about as long as the video.
* Samples open instantly from the right-hand panel; **Watch a demo** runs a scripted editing session.
* A rate limit (`RATE_LIMIT_PER_MIN`) protects the server.

## Keep it up
The demo must stay online until results are announced. Check `/api/v1/ready` before you submit and again a few days later; do not stop the container.
