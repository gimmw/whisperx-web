import os
import threading
import time
import traceback
import uuid
from json import dumps as json_dumps
from pathlib import Path
from typing import NamedTuple

import uvicorn
from fastapi import FastAPI, Form, Request, UploadFile, File
from hypy_utils import write_json
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

import metrics
from wp import AudioTooLong, diarized_transcribe

app = FastAPI()


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before anything can buffer them.

    Written as raw ASGI rather than a BaseHTTPMiddleware/@app.middleware
    function on purpose: this has to intercept the `receive` channel itself.
    Everything higher up -- route handlers, dependencies, Request.form() --
    only sees the body after Starlette's MultiPartParser has already pulled it
    off the wire and spooled it to a temporary file, which is exactly the cost
    being avoided.

    Two layers, because neither alone is sufficient:

    * Content-Length, when present and over the limit, lets us refuse without
      reading a single byte. It is client-supplied and therefore not
      trustworthy as an *upper* bound -- a liar can understate it -- but a
      client that declares an oversized body is telling the truth against its
      own interest, so it is safe to act on.
    * The streaming counter is the real enforcement: it covers chunked
      transfer encoding (no Content-Length at all) and any understated header,
      by cutting the stream the moment the running total passes the cap.
    """

    def __init__(self, app, max_bytes: int, paths: frozenset[str]):
        self.app = app
        self.max_bytes = max_bytes
        self.paths = paths

    async def __call__(self, scope, receive, send):
        # Only guard the upload endpoints. Everything else on this API has a
        # trivially small body, and wrapping receive for them would add work to
        # the once-per-second progress poll for no benefit.
        if scope["type"] != "http" or scope.get("path") not in self.paths:
            return await self.app(scope, receive, send)

        for key, value in scope.get("headers") or ():
            if key == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break
                if declared > self.max_bytes:
                    return await self._reject(send)
                break

        received = 0
        exceeded = False

        async def limited_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # Surfacing this as a disconnect stops the multipart
                    # parser mid-stream. It raises ClientDisconnect out of the
                    # app, which is caught below -- the alternative, feeding a
                    # truncated body through as if complete, would hand the
                    # handler a corrupt file and a misleading success.
                    return {"type": "http.disconnect"}
            return message

        response_started = False

        async def guarded_send(message):
            nonlocal response_started
            # Once the limit is blown, suppress whatever the app tries to say
            # so that the 413 below is the only response on the wire. Without
            # this, an app-level error response would be sent first and the
            # 413 would be a protocol violation.
            if exceeded:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            # A truncated body legitimately raises (ClientDisconnect, or a
            # parser error). That is the expected path here, not a fault worth
            # logging -- but only when we caused it.
            if not exceeded:
                raise

        if exceeded and not response_started:
            await self._reject(send)

    async def _reject(self, send):
        limit_mb = self.max_bytes // (1024 * 1024)
        body = json_dumps(
            {"error": f"File too large. Maximum upload size is {limit_mb} MB."}
        ).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                # The SPA reads res.error from the JSON body; keep CORS working
                # for the separately-exposed-backend deployment, since this
                # response bypasses CORSMiddleware entirely.
                (b"access-control-allow-origin", b"*"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


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

# Largest upload accepted, in bytes.
#
# Enforced by BodySizeLimitMiddleware below, NOT by the handler. The handler
# cannot do it: `file: UploadFile = File(...)` is a dependency, so FastAPI runs
# the multipart parser before the function body executes, and that parser
# consumes the entire request body into a SpooledTemporaryFile first. Any check
# written inside upload() -- on Content-Length or on the read() loop -- runs
# after the bytes are already on disk, and returns a correct-looking 413 while
# having prevented nothing. Measured: a 200 MB body against a 512 MB cap was
# fully received and spooled before the first line of the handler ran.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "512")) * 1024 * 1024

# Size of each chunk read from the request stream during upload.
UPLOAD_CHUNK_BYTES = 1024 * 1024

# Installed last so it ends up OUTERMOST: add_middleware prepends, so the
# most recently added wrapper is entered first. The size guard must see the
# request before CORSMiddleware and before routing, since the whole point is to
# act ahead of the multipart parser.
#
# Scoped to the upload path only. "/upload" is what the frontend's nginx passes
# through after stripping the /api prefix; "/api/upload" covers the backend
# being exposed directly without that rewrite.
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=MAX_UPLOAD_BYTES,
    paths=frozenset({"/upload", "/api/upload"}),
)

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

# Upper bound on the speaker-count hints, mirroring the max="20" on the number
# inputs in src/lib/Home.svelte.
#
# Those attributes are advisory: the browser will not submit a larger value
# through the UI, but nothing stops a direct POST, so the real check has to be
# here. Diarising a recording with more than this many distinct speakers is
# well outside what this service is for, and the value only ever narrows the
# search, so a generous ceiling costs nothing.
MAX_SPEAKERS = 20

# Language codes accepted, mirroring the picker in src/lib/Home.svelte plus
# "auto" for detection. Whisper's own set, so anything outside it is a client
# bug or a probe rather than a usable request.
#
# Not a shell-injection concern -- nothing here reaches a shell -- but the value
# is passed straight to the model, and an unrecognised code silently produces
# nonsense output rather than an error. Rejecting it up front turns a confusing
# transcript into a clear 400.
ALLOWED_LANGUAGES = {
    "auto",
    "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr", "pl",
    "ca", "nl", "ar", "sv", "it", "id", "hi", "fi", "vi", "he", "uk",
    "el", "ms", "cs", "ro", "da", "hu", "ta", "no", "th", "ur", "hr",
    "bg", "lt", "la", "mi", "ml", "cy", "sk", "te", "fa", "lv", "bn",
    "sr", "az", "sl", "kn", "et", "mk", "br", "eu", "is", "hy", "ne",
    "mn", "bs", "kk", "sq", "sw", "gl", "mr", "pa", "si", "km", "sn",
    "yo", "so", "af", "oc", "ka", "be", "tg", "sd", "gu", "am", "yi",
    "lo", "uz", "fo", "ht", "ps", "tk", "nn", "mt", "sa", "lb", "my",
    "bo", "tl", "mg", "as", "tt", "haw", "ln", "ha", "ba", "jw", "su",
    "yue",
}

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


class SpeakerCountError(ValueError):
    """Raised when a speaker-count hint is unusable. Message is client-safe."""


def _parse_speaker_count(raw: str | None, field: str) -> int | None:
    """Validate one of the min/max speaker hints.

    Returns None for "no preference", which is what pyannote wants for
    autodetection. Both an absent field and an explicit 0 map to None:
    pyannote's set_num_speakers does `min_speakers = num_speakers or
    min_speakers or 1`, so a falsy 0 already falls through to the default --
    normalising it here makes that behaviour intentional and explicit rather
    than an accident of truthiness, and keeps 0 working as "autodetect" for
    clients that send it.

    Anything else must be a plain integer in [1, MAX_SPEAKERS]. Bare int()
    would accept values pyannote does not defend against: negatives survive
    set_num_speakers untouched (verified: min=-5 yields bounds [-5, inf]), and
    while the clustering stage happens to clamp them to >=1 today, that is an
    implementation detail two libraries down, not a contract.
    """
    if raw is None:
        return None

    raw = raw.strip()
    if not raw:
        return None

    # Require plain ASCII digits rather than relying on int(), which also
    # accepts underscore separators ("1_0" -> 10), a leading sign, and
    # non-ASCII decimal digits ("١٢" -> 12). None of those are dangerous here
    # -- the range check below bounds the result either way -- but a field the
    # UI renders as <input type="number"> should not quietly reinterpret them.
    digits = raw[1:] if raw[0] == "-" else raw
    if not (digits.isascii() and digits.isdigit()):
        raise SpeakerCountError(f"{field} must be a whole number.")

    # Checked before int() so a negative gets the specific message rather than
    # the generic one, which would be actively misleading -- "-5" *is* a whole
    # number, it is just not a usable speaker count.
    if raw[0] == "-" and any(c != "0" for c in digits):
        raise SpeakerCountError(f"{field} cannot be negative.")

    value = int(raw)

    # Explicit "no preference".
    if value == 0:
        return None

    if value > MAX_SPEAKERS:
        raise SpeakerCountError(f"{field} cannot be greater than {MAX_SPEAKERS}.")

    return value


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

# Failed audio_ids, mapped to a client-safe explanation or None.
#
# None means "server fault", and the client is shown GENERIC_ERROR: the
# exception detail is never exposed, since a traceback would disclose absolute
# filesystem paths, dependency versions and internal structure. The full
# traceback goes to the server log instead, keyed by audio_id so an operator can
# correlate a user's report with it.
#
# A string is used only for failures caused by the *input* -- currently just an
# over-long file. Those are the user's to fix, and telling them to contact an
# administrator would be actively unhelpful. Every such string is written here
# in this file, never derived from an exception message.
errors: dict[str, str | None] = {}

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
        min_spk = _parse_speaker_count(min_speakers, "min_speakers")
        max_spk = _parse_speaker_count(max_speakers, "max_speakers")
    except SpeakerCountError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    # Reject an inverted range here rather than letting pyannote raise it.
    # set_num_speakers does raise ValueError for min > max, but that happens on
    # the worker thread after the upload has been stored and queued -- the user
    # would wait through the queue only to be told their request was invalid,
    # and the failure would surface as the generic server-error message since
    # nothing distinguishes it from a real fault. Both being None (autodetect)
    # skips this, as does either one alone.
    if min_spk is not None and max_spk is not None and min_spk > max_spk:
        return JSONResponse(
            status_code=400,
            content={"error": "min_speakers cannot be greater than max_speakers."},
        )

    language = (language or "").strip().lower()
    if language not in ALLOWED_LANGUAGES:
        return JSONResponse(
            status_code=400,
            content={"error": "Unsupported language code."},
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
        # the whole upload in memory a second time.
        #
        # The size check here is a backstop, not the enforcement:
        # BodySizeLimitMiddleware has already capped the body before this
        # handler was reached, so in normal operation `written` cannot exceed
        # the limit. It is kept so that the guarantee does not rest solely on
        # the middleware still being installed and correctly scoped -- if the
        # path allowlist above ever drifts from the real route, this is what
        # stops an unbounded write to the data volume.
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
        # Either a fixed message written in this file (input problems) or the
        # generic one; never exception text. See the note on `errors`. The id is
        # echoed so the user can quote it and an operator can find the trace.
        return {
            "done": False,
            "state": "error",
            "status": errors[uuid] or GENERIC_ERROR,
            "error_id": uuid,
        }
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

        except AudioTooLong as e:
            # The user's file is the problem, so say so plainly. The message is
            # built in wp.py from configured limits and the probed duration --
            # no exception text or path from an underlying library.
            errors[audio_id] = str(e)
            print(f"Rejected {audio_id}: {e}")

        except Exception:
            # Record only that this id failed; the diagnostic detail stays
            # server-side, keyed by the same id the client is shown.
            errors[audio_id] = None
            print(f"Error processing {audio_id}:")
            traceback.print_exc()

        finally:
            # The source audio is dead once transcription returns: it is read
            # exactly once (load_audio_bounded in wp.py) and nothing serves or
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
