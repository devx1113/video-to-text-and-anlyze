"""
Video -> Text transcriber, optimized for speed.

Pipeline:
  1. Browser uploads a video (or audio) file to this local server.
  2. ffmpeg extracts a compact 16 kHz mono WAV from it (fast, local).
  3. The audio is sent to Azure AI Speech "Fast Transcription" REST API,
     which transcribes much faster than real time and returns the full text
     in a single synchronous response.

Run:
  python app.py
then open http://127.0.0.1:8000
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import imageio_ffmpeg

load_dotenv()

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "").strip()
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "").strip()

# OpenAI — used by the "Analyze" feature to reason over a transcript.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Fast Transcription endpoint (synchronous, far faster than real-time).
# Docs: https://learn.microsoft.com/azure/ai-services/speech-service/fast-transcription-create
API_VERSION = "2024-11-15"
TRANSCRIBE_URL = (
    f"https://{AZURE_SPEECH_REGION}.api.cognitive.microsoft.com"
    f"/speechtotext/transcriptions:transcribe?api-version={API_VERSION}"
)

# Map UI choice -> Azure locale list. "auto" passes candidates so Azure
# performs language identification across the two languages you use.
LOCALE_MAP = {
    "en-US": ["en-US"],
    "ja-JP": ["ja-JP"],
    "auto": ["en-US", "ja-JP"],
}

# Prefer a system ffmpeg if present (e.g. apt-installed in the Docker image),
# otherwise fall back to the binary bundled by imageio-ffmpeg (no sudo needed).
FFMPEG = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()

HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Video → Text (Azure Fast Transcription)")


def extract_audio(src_path: str, dst_path: str) -> None:
    """Extract mono 16 kHz 16-bit PCM WAV from any video/audio file."""
    cmd = [
        FFMPEG,
        "-y",
        "-i", src_path,
        "-vn",            # drop video
        "-ac", "1",       # mono
        "-ar", "16000",   # 16 kHz (speech-optimal)
        "-c:a", "pcm_s16le",
        dst_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Surface the tail of ffmpeg's stderr — usually says what went wrong.
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise HTTPException(
            status_code=400,
            detail="Could not extract audio (is this a valid media file?): "
            + " ".join(tail),
        )


def call_azure(audio_path: str, locales: list[str]) -> dict:
    """Send the audio to Azure Fast Transcription and return the parsed JSON."""
    definition = {
        "locales": locales,
        "profanityFilterMode": "None",
    }
    with open(audio_path, "rb") as fh:
        files = {
            "audio": ("audio.wav", fh, "audio/wav"),
            "definition": (None, json.dumps(definition), "application/json"),
        }
        headers = {"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY}
        resp = requests.post(TRANSCRIBE_URL, headers=headers, files=files, timeout=600)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Azure returned {resp.status_code}: {resp.text[:500]}",
        )
    return resp.json()


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


def call_openai(prompt: str, transcript: str) -> str:
    """Send the user's prompt + transcript to OpenAI and return the answer text."""
    user_content = prompt
    if transcript.strip():
        user_content = f"{prompt}\n\n--- Transcript ---\n{transcript}"
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that analyzes "
                "transcripts. Follow the user's instruction using the transcript "
                "provided. If no transcript is given, just answer the instruction.",
            },
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI returned {resp.status_code}: {resp.text[:500]}",
        )
    return resp.json()["choices"][0]["message"]["content"]


@app.get("/health")
def health():
    return {
        "ok": bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION),
        "region": AZURE_SPEECH_REGION or None,
        "key_set": bool(AZURE_SPEECH_KEY),
        "openai_key_set": bool(OPENAI_API_KEY),
        "openai_model": OPENAI_MODEL,
        "ffmpeg": FFMPEG,
    }


@app.post("/analyze")
async def analyze(prompt: str = Form(...), transcript: str = Form("")):
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=500, detail="OPENAI_API_KEY not set in .env"
        )
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is empty")
    answer = call_openai(prompt, transcript)
    return JSONResponse({"answer": answer, "model": OPENAI_MODEL})


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form("auto")):
    if not AZURE_SPEECH_KEY or not AZURE_SPEECH_REGION:
        raise HTTPException(
            status_code=500,
            detail="AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set in .env",
        )
    locales = LOCALE_MAP.get(language, LOCALE_MAP["auto"])

    t0 = time.time()
    workdir = tempfile.mkdtemp(prefix="vtt_")
    src = os.path.join(workdir, file.filename or "input")
    wav = os.path.join(workdir, "audio.wav")
    try:
        with open(src, "wb") as out:
            out.write(await file.read())

        extract_audio(src, wav)
        t_extract = time.time()

        data = call_azure(wav, locales)
        t_done = time.time()

        combined = data.get("combinedPhrases") or []
        text = combined[0].get("text", "") if combined else ""
        audio_ms = data.get("durationMilliseconds", 0)

        return JSONResponse(
            {
                "text": text,
                "language_requested": language,
                "audio_seconds": round(audio_ms / 1000, 1),
                "extract_seconds": round(t_extract - t0, 1),
                "transcribe_seconds": round(t_done - t_extract, 1),
                "total_seconds": round(t_done - t0, 1),
            }
        )
    finally:
        for p in (src, wav):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
