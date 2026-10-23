"""Optional ElevenLabs voice for the web simulator.

The simulator posts a reply's text to /tts and plays the MP3 it gets back.
The API key stays on the server (env ELEVENLABS_API_KEY); without it /tts is
off and the simulator uses the browser's own voice. Real Alexa+ speaks with
Alexa's voice, so this only changes the simulator.

Because the server is public, each request is capped in length, each client
is rate limited, and the server stops calling ElevenLabs after a character
budget, so a stranger can't spend the account's credits.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict, defaultdict, deque

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger("pantrypilot.tts")

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_64"
DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"   # "Sarah", one of ElevenLabs' premade voices
DEFAULT_MODEL = "eleven_flash_v2_5"      # fastest model, and half the credits per character
MAX_TEXT = 400                           # longest reply PantryPilot speaks is ~300 characters
RATE_LIMIT = 20                          # requests per client ...
RATE_WINDOW = 600                        # ... per 10 minutes
CACHE_SIZE = 64

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_cache: "OrderedDict[str, bytes]" = OrderedDict()
_chars_used = 0


def _key() -> str:
    return os.getenv("ELEVENLABS_API_KEY", "").strip()


def _budget() -> int:
    """Characters this server may send to ElevenLabs before it stops (resets on restart)."""
    try:
        return int(os.getenv("ELEVENLABS_CHAR_BUDGET", "8000"))
    except ValueError:
        return 8000


def enabled() -> bool:
    return bool(_key()) and _chars_used < _budget()


def reset() -> None:
    """Clear limits and cache (used by tests)."""
    global _chars_used
    with _lock:
        _hits.clear()
        _cache.clear()
        _chars_used = 0


def _allow(client: str) -> bool:
    now = time.monotonic()
    with _lock:
        q = _hits[client]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return False
        q.append(now)
        return True


def synthesize(text: str) -> bytes | None:
    """Return MP3 audio for text, or None if ElevenLabs is off, over budget or failing."""
    global _chars_used
    with _lock:
        if text in _cache:
            _cache.move_to_end(text)
            return _cache[text]
        if not _key() or _chars_used + len(text) > _budget():
            return None
        _chars_used += len(text)
    voice = os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_VOICE)
    body = json.dumps({
        "text": text,
        "model_id": os.getenv("ELEVENLABS_MODEL_ID", DEFAULT_MODEL),
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }).encode()
    req = urllib.request.Request(
        API_URL.format(voice=voice), data=body, method="POST",
        headers={"xi-api-key": _key(), "Content-Type": "application/json",
                 "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        log.warning("ElevenLabs returned %s: %s", exc.code, exc.read()[:200])
        return None
    except Exception as exc:  # network errors, timeouts
        log.warning("ElevenLabs call failed: %s", type(exc).__name__)
        return None
    with _lock:
        _cache[text] = audio
        if len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return audio


def _client(request: Request) -> str:
    # App Runner sits behind a proxy, so the caller is the first X-Forwarded-For hop.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def status_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"enabled": enabled(), "provider": "elevenlabs"})


async def tts_endpoint(request: Request) -> Response:
    if not enabled():
        return JSONResponse({"error": "tts disabled"}, status_code=503)
    try:
        text = str((await request.json()).get("text", "")).strip()
    except Exception:
        return JSONResponse({"error": "send JSON {\"text\": ...}"}, status_code=400)
    if not text or len(text) > MAX_TEXT:
        return JSONResponse({"error": f"text must be 1 to {MAX_TEXT} characters"}, status_code=400)
    if not _allow(_client(request)):
        return JSONResponse({"error": "too many requests"}, status_code=429)
    audio = await run_in_threadpool(synthesize, text)
    if audio is None:
        return JSONResponse({"error": "tts unavailable"}, status_code=503)
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
