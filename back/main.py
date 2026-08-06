import os
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import NamedTuple

import uvicorn
from fastapi import FastAPI, Form, Request, UploadFile, File
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
TMP_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = Path("/ws/tmp-whisper")

# Both subdirectories, not just DATA_DIR itself.
#
# The Dockerfile creates these, but a volume mounted at /ws/tmp-whisper shadows
# whatever the image layer had there, so on a freshly provisioned PVC they are
# absent. The StaticFiles mount below resolves its directory when it is
# constructed -- at import time -- and raises RuntimeError if it is missing, so
# a new PVC would crash the process before uvicorn ever bound a port.
#
# parents=True because DATA_DIR itself may also be new; exist_ok=True so a
# restart onto an existing volume is a no-op.
for _sub in ("audio", "transcription"):
    (DATA_DIR / _sub).mkdir(parents=True, exist_ok=True)

# Largest upload accepted, in bytes. Enforced while streaming so an oversized
# body is abandoned early rather than after it has already been buffered.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "512")) * 1024 * 1024

# Size of each chunk read from the request stream during upload.
UPLOAD_CHUNK_BYTES = 1024 * 1024

# Maximum number of jobs waiting or running at once, across all clients.
#
# The worker is strictly serial (one GPU, one thread), so the worst-case wait
# for the last person in line is roughly this many jobs times the median job
# duration. Raising it does not increase throughput -- it only lengthens that
# wait while consuming more disk, so keep it small enough that a full queue is
# still worth waiting in.
MAX_QUEUE_DEPTH = int(os.getenv("MAX_QUEUE_DEPTH", "10"))

# Maximum jobs a single client may have queued or processing at once.
#
# This is a fairness control rather than a rate limit: it bounds the contended
# resource (GPU time) directly, and it clears itself as jobs finish, so there
# is no time window to track and no penalty that outlives the work.
MAX_JOBS_PER_CLIENT = int(os.getenv("MAX_JOBS_PER_CLIENT", "2"))

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


def _client_key(request: Request) -> str:
    """Identify the caller for per-client quota purposes.

    request.client.host is NOT usable directly: in the normal deployment the
    only peer this process ever sees is the nginx container, so every user
    would collapse into a single key and one person's uploads would quota out
    everyone. uvicorn only honours forwarding headers from
    `forwarded_allow_ips` (default 127.0.0.1), which the proxy -- a separate
    container -- is not, so its own de-proxying does not apply either.

    nginx sets X-Real-IP with proxy_set_header (see front/nginx.conf), which
    *replaces* rather than appends, so a client cannot forge it through the
    proxy. X-Forwarded-For is deliberately not used: it is a client-extendable
    list where only the rightmost entry is trustworthy.

    This holds only while the backend is unreachable except through the proxy
    -- which is why compose.yaml publishes no ports for it. If the backend is
    ever exposed directly, this header becomes attacker-controlled and this
    quota turns into a trivially bypassed one.
    """
    forwarded = (request.headers.get("x-real-ip") or "").strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


process_queue = []
processing = ""
processing_client = ""
start_time = 0
lock = threading.Lock()

# Slots claimed by uploads that are still streaming to disk and have not yet
# reached the queue.
#
# Without this, the capacity check and the queue append are separate operations
# with a long upload in between, so N concurrent uploads all observe a queue
# below the cap and all proceed -- admitting N jobs past a cap of 1. Reserving
# up front closes that window: the slot is counted from the moment the request
# is admitted, not from the moment it finishes uploading.
#
# Keyed by client, and the per-client counts are derived from the union of
# these and the jobs actually in the system.
reserved: dict[str, int] = {}

# Set of audio_ids whose processing failed. Only membership is tracked, never
# the exception detail: this is served to unauthenticated clients, and a
# traceback would disclose absolute filesystem paths, dependency versions and
# internal structure. The full traceback goes to the server log instead, keyed
# by audio_id so an operator can correlate a user's report with it.
errors: set[str] = set()

# Message shown to clients for any server-side failure. Deliberately fixed and
# uninformative -- the audio_id accompanying it is what makes a report
# actionable, not the text.
GENERIC_ERROR = "Transcription failed. Please try again, or contact the administrator with this ID."

app.mount("/result", StaticFiles(directory=DATA_DIR / "transcription"), name="result")


class PendingProcess(NamedTuple):
    audio_id: str
    file: Path
    language: str = "en"
    diarize: bool = True
    min_speakers: int | None = None
    max_speakers: int | None = None
    initial_prompt: str = ""
    client: str = ""


def _total_jobs() -> int:
    """Jobs occupying capacity: queued, uploading, and the one processing.

    Caller must hold `lock`.
    """
    return len(process_queue) + sum(reserved.values()) + (1 if processing else 0)


def _owned_by(client: str) -> int:
    """Jobs belonging to `client` in any of those three states.

    Caller must hold `lock`.
    """
    queued = sum(1 for p in process_queue if p.client == client)
    running = 1 if processing and processing_client == client else 0
    return queued + reserved.get(client, 0) + running


def _try_reserve(client: str) -> str | None:
    """Claim a capacity slot for `client`.

    Returns None on success, or a short reason ("busy" / "per_client") on
    refusal. Both the check and the claim happen under one lock acquisition so
    concurrent uploads cannot both pass a check that only one should.
    """
    with lock:
        if _total_jobs() >= MAX_QUEUE_DEPTH:
            return "busy"
        if _owned_by(client) >= MAX_JOBS_PER_CLIENT:
            return "per_client"
        reserved[client] = reserved.get(client, 0) + 1
        return None


def _drop_reservation_locked(client: str) -> None:
    """Decrement a client's reservation count. Caller must hold `lock`.

    Entries are deleted at zero rather than left at 0, so `reserved` does not
    accumulate one key per client IP ever seen.
    """
    remaining = reserved.get(client, 0) - 1
    if remaining > 0:
        reserved[client] = remaining
    else:
        reserved.pop(client, None)


def _release_reservation(client: str) -> None:
    """Drop a slot claimed by _try_reserve, for uploads that never queued."""
    with lock:
        _drop_reservation_locked(client)


def _commit_reservation(pending: PendingProcess) -> None:
    """Turn a reservation into a real queue entry.

    Done under a single lock acquisition: releasing first and appending after
    would briefly drop the client below their quota, letting a concurrent
    request slip in and overshoot the cap.
    """
    with lock:
        _drop_reservation_locked(pending.client)
        process_queue.append(pending)


@app.get('/health')
def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload(
    request: Request,
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

    # Claim a capacity slot before reading the body. Checking after the upload
    # would mean a rejected request had already cost a full MAX_UPLOAD_MB of
    # bandwidth and disk -- which is exactly the resource the cap exists to
    # protect.
    client = _client_key(request)
    refusal = _try_reserve(client)
    if refusal == "busy":
        return JSONResponse(
            status_code=503,
            content={"error": "The server is at capacity. Please try again in a few minutes."},
            # Advisory only, but lets well-behaved clients back off sensibly
            # instead of retrying immediately.
            headers={"Retry-After": "120"},
        )
    if refusal == "per_client":
        return JSONResponse(
            status_code=429,
            content={
                "error": f"You already have {MAX_JOBS_PER_CLIENT} transcriptions in progress. "
                         "Please wait for one to finish before uploading another."
            },
        )

    # From here the slot is held, so every exit must either commit it (the job
    # entered the queue and now occupies capacity in its own right) or release
    # it. The finally below covers the paths that did neither -- including a
    # client that disconnects mid-upload, which raises out of file.read() and
    # would otherwise strand the slot until restart.
    committed = False
    try:
        time_str = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{time_str}] Received {file.filename!r} ({ext})")

        audio_id = str(uuid.uuid4())
        fp = DATA_DIR / "audio" / f"{audio_id}.{ext}"
        fp.parent.mkdir(parents=True, exist_ok=True)

        # Stream to disk in chunks rather than file.read(), which would buffer
        # the entire upload in memory. The running total is checked as we go so
        # an oversized body is rejected without ever being fully received or
        # stored.
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
        except Exception:
            fp.unlink(missing_ok=True)
            # Log the detail, return none of it: this path catches arbitrary
            # exceptions (disk full, permission denied) whose messages embed
            # absolute paths and other internals.
            print(f"Error saving upload {audio_id}:")
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"error": "Could not save the uploaded file. Please try again."},
            )

        if written == 0:
            fp.unlink(missing_ok=True)
            return JSONResponse(status_code=400, content={"error": "Uploaded file is empty"})

        do_diarize = diarize.lower() == "true"

        # Hand the reservation over to the queue entry in one step.
        _commit_reservation(PendingProcess(
            audio_id, fp, language, do_diarize, min_spk, max_spk,
            initial_prompt.strip(), client,
        ))
        committed = True

        return {"audio_id": audio_id}
    finally:
        if not committed:
            _release_reservation(client)


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
        # No exception detail here: see the note on `errors`. The id is echoed
        # so the user can quote it and an operator can find the logged trace.
        return {"done": False, "state": "error", "status": GENERIC_ERROR, "error_id": uuid}
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
    global processing, processing_client, start_time
    while True:
        time.sleep(0.1)
        with lock:
            if len(process_queue) > 0:
                pending = process_queue.pop(0)
                audio_id = pending.audio_id
                processing = audio_id
                # Tracked so the running job still counts against its owner's
                # quota; without this a client could queue another the instant
                # theirs left the queue and started processing.
                processing_client = pending.client
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

        except Exception:
            # Record only that this id failed; the diagnostic detail stays
            # server-side, keyed by the same id the client is shown.
            errors.add(audio_id)
            print(f"Error processing {audio_id}:")
            traceback.print_exc()

        finally:
            # The source audio is dead once transcription returns: it is read
            # exactly once (whisperx.load_audio in wp.py) and nothing serves or
            # re-reads it afterwards -- only the transcript is downloadable.
            # Deleting it here rather than on a schedule keeps peak disk bounded
            # by the queue depth instead of by the cleanup interval, and keeps
            # raw voice recordings on disk for the shortest time possible.
            #
            # In `finally` so a failed job cleans up too: those files would
            # otherwise be the ones that linger, since nothing will ever revisit
            # them.
            #
            # Never let cleanup raise: this runs on the single worker thread, so
            # an unhandled exception here would end the thread and silently
            # freeze the queue for every subsequent job.
            try:
                pending.file.unlink(missing_ok=True)
            except Exception:
                print(f"Could not delete audio for {audio_id}:")
                traceback.print_exc()

        # Clear processing, freeing both the global slot and the owner's quota.
        with lock:
            processing = ""
            processing_client = ""
            start_time = 0


if __name__ == '__main__':
    metrics.start_sampler()
    threading.Thread(target=process, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=49585)
    print("Server started")
