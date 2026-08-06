import os
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import NamedTuple

import uvicorn
from fastapi import FastAPI, Form, UploadFile, File
from hypy_utils import write_json
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

import metrics
from wp import diarized_transcribe

app = FastAPI()

# CORS
#
# Not needed in the normal deployment: the frontend proxies /api to this
# service, so the browser stays on a single origin and CORS never applies.
# Configurable for the case where the backend is exposed separately (e.g. its
# own Ingress host), which makes requests genuinely cross-origin.
#
# Defaults to "*" rather than something restrictive because the correct value
# depends entirely on the cluster's Ingress hostname, which this code cannot
# know. See the note below on why that default is not the security risk it
# looks like.
#
# NOTE: this is not an authentication boundary. The API has no accounts,
# cookies or tokens, so CORS only constrains browsers; a direct client (curl,
# a script) is unaffected regardless of what is configured here. The real
# limits on abuse are the upload size cap and extension allowlist below.
#
# allow_credentials is deliberately False: nothing in this API uses cookies or
# an Authorization header, and setting it True alongside allow_origins=["*"]
# makes Starlette reflect the caller's Origin back with
# Access-Control-Allow-Credentials: true — the most permissive combination
# possible, and the one the CORS spec forbids expressing literally.
_cors_origins = os.getenv("CORS_ORIGINS", "*")
# Origin headers never carry a path, so a configured trailing slash would
# silently never match. Normalise it away.
ALLOW_ORIGINS = [o.strip().rstrip("/") for o in _cors_origins.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

TMP_DIR = Path("/tmp/whisper")
TMP_DIR.mkdir(exist_ok=True)
DATA_DIR = Path("/ws/tmp-whisper")
DATA_DIR.mkdir(exist_ok=True)

# Largest upload accepted, in bytes. Enforced while streaming so an oversized
# body is abandoned early rather than after it has already been buffered.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "512")) * 1024 * 1024

# Size of each chunk read from the request stream during upload.
UPLOAD_CHUNK_BYTES = 1024 * 1024

# Extensions accepted for upload. The uploaded name is attacker-controlled and
# is used to build a filesystem path, so only these exact values are ever
# interpolated — never the raw client string.
ALLOWED_EXTENSIONS = {
    # audio
    "mp3", "wav", "m4a", "flac", "ogg", "oga", "opus", "aac", "wma", "aiff", "aif",
    # video (ffmpeg extracts the audio track)
    "mp4", "mkv", "mov", "avi", "webm", "m4v", "mpg", "mpeg", "wmv", "flv", "3gp",
}


def _safe_extension(filename: str | None) -> str | None:
    """Extract a validated lowercase extension from a client-supplied filename.

    Returns None if the extension is missing or not allowlisted. Path
    separators are stripped first: a name like "x.../../../etc/passwd" would
    otherwise yield "/etc/passwd" from a naive rsplit, which then escapes
    DATA_DIR when interpolated into a path.
    """
    if not filename:
        return None

    # Strip any directory component, handling both POSIX and Windows separators
    # since the client controls this string and is not necessarily POSIX.
    base = os.path.basename(filename.replace("\\", "/")).strip()
    if "." not in base:
        return None

    ext = base.rsplit(".", 1)[-1].lower()
    return ext if ext in ALLOWED_EXTENSIONS else None


process_queue = []
processing = ""
start_time = 0
lock = threading.Lock()
errors = {}

app.mount("/result", StaticFiles(directory=DATA_DIR / "transcription"), name="result")


class PendingProcess(NamedTuple):
    audio_id: str
    file: Path
    language: str = "en"
    diarize: bool = True
    min_speakers: int | None = None
    max_speakers: int | None = None
    initial_prompt: str = ""


@app.get('/health')
def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    language: str = Form("en"),
    diarize: str = Form("true"),
    min_speakers: str | None = Form(None),
    max_speakers: str | None = Form(None),
    initial_prompt: str = Form(""),
):
    # Validate the filename before touching the disk. Only the allowlisted
    # extension is ever used to build the path; the rest of the client-supplied
    # name is discarded.
    ext = _safe_extension(file.filename)
    if ext is None:
        return JSONResponse(
            status_code=400,
            content={"error": "Unsupported file type. Please upload an audio or video file."},
        )

    # Parse diarization options before writing anything, so bad input fails
    # without leaving an orphaned file behind.
    try:
        min_spk = int(min_speakers) if min_speakers else None
        max_spk = int(max_speakers) if max_speakers else None
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "min_speakers and max_speakers must be integers"},
        )

    time_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{time_str}] Received {file.filename!r} ({ext})")

    audio_id = str(uuid.uuid4())
    fp = DATA_DIR / "audio" / f"{audio_id}.{ext}"
    fp.parent.mkdir(parents=True, exist_ok=True)

    # Stream to disk in chunks rather than file.read(), which would buffer the
    # entire upload in memory. The running total is checked as we go so an
    # oversized body is rejected without ever being fully received or stored.
    written = 0
    try:
        with open(fp, "wb") as out:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise ValueError("too large")
                out.write(chunk)
    except ValueError:
        fp.unlink(missing_ok=True)
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return JSONResponse(
            status_code=413,
            content={"error": f"File too large. Maximum upload size is {limit_mb} MB."},
        )
    except Exception as e:
        fp.unlink(missing_ok=True)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

    if written == 0:
        fp.unlink(missing_ok=True)
        return JSONResponse(status_code=400, content={"error": "Uploaded file is empty"})

    do_diarize = diarize.lower() == "true"

    # Add to processing queue
    with lock:
        process_queue.append(PendingProcess(audio_id, fp, language, do_diarize, min_spk, max_spk, initial_prompt.strip()))

    return {"audio_id": audio_id}


@app.get("/progress/{uuid}")
def progress(uuid: str):
    if Path(DATA_DIR / "transcription" / f"{uuid}.json").exists():
        return {"done": True}

    with lock:
        current, started_at = processing, start_time

    if current == uuid:
        # Metrics are returned as structured numbers rather than a pre-rendered
        # string so the client can format/visualise them, and so that a missing
        # value is representable as null instead of a fabricated zero.
        m = metrics.collect()
        elapsed = time.time() - started_at
        return {
            "done": False,
            "state": "processing",
            "elapsed": elapsed,
            "metrics": m,
            # Human-readable fallback for older clients.
            "status": _status_line(m, elapsed),
        }
    elif uuid in errors:
        return {"done": False, "state": "error", "status": "Error", "error": errors[uuid]}
    else:
        index = 0
        with lock:
            for i, pending in enumerate(process_queue):
                if pending.audio_id == uuid:
                    index = i
                    break
        return {
            "done": False,
            "state": "queued",
            "queue_position": index,
            "status": f"Queued ({index} in queue before this one)",
        }


def _status_line(m: dict, elapsed: float) -> str:
    parts = []
    if m.get("cpu_cores_used") is not None:
        parts.append(f"{m['cpu_cores_used']:.2f} CPU cores")
    if m.get("gpu_util") is not None:
        parts.append(f"{m['gpu_util'] * 100:.0f}% GPU")
    parts.append(f"{elapsed:.0f}s elapsed")
    return f"Processing ({', '.join(parts)})"


def process():
    global processing, start_time
    while True:
        time.sleep(0.1)
        with lock:
            if len(process_queue) > 0:
                pending = process_queue.pop(0)
                audio_id = pending.audio_id
                processing = audio_id
                start_time = time.time()
            else:
                continue

        try:
            # Start transcription
            output, elapsed = diarized_transcribe(
                pending.file,
                language=pending.language,
                diarize=pending.diarize,
                min_speakers=pending.min_speakers,
                max_speakers=pending.max_speakers,
                initial_prompt=pending.initial_prompt,
                task="transcribe",
            )

            # Write to file
            write_json(DATA_DIR / "transcription" / f"{audio_id}.json", {
                "output": output,
                "elapsed": elapsed
            })

        except Exception as e:
            errors[audio_id] = str(e) + "\n\n" + traceback.format_exc()
            print(f"Error processing {audio_id}: {e}")

        # Clear processing
        with lock:
            processing = ""
            start_time = 0


if __name__ == '__main__':
    metrics.start_sampler()
    threading.Thread(target=process, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=49585)
    print("Server started")
