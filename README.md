# Video → Text (fast)

Local web tool that transcribes the speech in a **video** (or audio) file to text,
optimized for **speed** using Azure AI Speech **Fast Transcription** (transcribes
much faster than real time, in one synchronous request).

## How it works

```
browser ──upload──▶ FastAPI ──ffmpeg──▶ 16kHz mono WAV ──REST──▶ Azure Fast Transcription ──▶ text
```

- `ffmpeg` is provided by the `imageio-ffmpeg` pip package — **no system install / sudo**.
- Language is chosen in the page: **English**, **Japanese**, or **Auto-detect** (both).

## Setup

```bash
cd /home/devx/Documents/audio-extracting
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`.env` must contain (already set):

```
AZURE_SPEECH_KEY=<your key>
AZURE_SPEECH_REGION=eastus
OPENAI_API_KEY=<your key>      # for the "Analyze" feature
OPENAI_MODEL=gpt-4o-mini       # optional, defaults to gpt-4o-mini
```

## Analyze feature

Below the transcript there's an **Analyze the transcript with AI** panel: type a
prompt (e.g. "Summarize in 5 bullets", "Extract action items", "Translate to
English") and the current transcript + your prompt are sent to OpenAI
(`POST /analyze`); the answer shows in a copyable box.

## Run

```bash
python app.py
```

Open http://127.0.0.1:8765 — pick the language, choose a file, click **Transcribe**.

`GET /health` confirms the key/region are loaded.

## Run with Docker (recommended)

The image bundles ffmpeg + the app. Your `.env` is **not** baked into the image —
it's loaded at runtime, so secrets stay out of the image layers.

```bash
# build + start (reads .env for the Azure key/region)
docker compose up -d --build

# open http://127.0.0.1:8765
docker compose logs -f      # view logs
docker compose down         # stop
```

Or with plain Docker:

```bash
docker build -t video-to-text .
docker run --rm -p 8765:8765 --env-file .env video-to-text
```

Notes:
- The container binds to `0.0.0.0:8765` internally; the host reaches it on
  `http://127.0.0.1:8765` via the published port.
- `.env` is in `.dockerignore`, so it's never copied into the image — pass it
  with `env_file:` (compose) or `--env-file` (docker run) instead.

## Limits & notes

- Fast Transcription handles files up to ~2 hours / 300 MB per request. For longer
  videos you'd split the audio into chunks (not yet implemented).
- The `.env` key is a secret — it's gitignored here. Rotate it in the Azure portal
  if it has been exposed.
- Region must be `eastus` (or wherever your Speech resource lives); it sets the endpoint.
