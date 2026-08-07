import gc
import subprocess
import time
from pathlib import Path

import numpy as np
import os
import torch
import whisperx
from whisperx.diarize import DiarizationPipeline

DEVICE = "cuda"
COMPUTE_TYPE = "float16"
BATCH_SIZE = 16
HF_TOKEN = os.getenv("HF_TOKEN")

# Sample rate whisper expects; also what the decode below resamples to.
SAMPLE_RATE = 16000

# Longest audio accepted, in seconds (default 4h).
#
# This is a memory control, not a policy one. Decoded audio is float32 mono at
# 16 kHz -- 64 KB per second -- and the container format says nothing about how
# much that expands to: 2h of silence fits in a 2.6 MB Opus file that decodes to
# 439 MB, a ~170x amplification. MAX_UPLOAD_MB cannot bound this because the
# limit is on the *compressed* size. Without a duration cap a handful of small,
# entirely valid uploads can OOM the pod.
MAX_AUDIO_SECONDS = float(os.getenv("MAX_AUDIO_SECONDS", str(4 * 3600)))

# Wall-clock ceiling for the two ffmpeg subprocesses.
#
# Neither has a timeout upstream (whisperx's load_audio calls subprocess.run
# with no timeout=). A malformed file that makes ffmpeg spin would hang the
# single worker thread forever, permanently wedging the queue for every
# subsequent job -- a worse outcome than the job simply failing.
FFPROBE_TIMEOUT = 30
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "1800"))


class AudioTooLong(Exception):
    """Raised when a file's duration exceeds MAX_AUDIO_SECONDS."""


def _fmt_duration(seconds: float) -> str:
    """Human-readable duration. Always hours would render a 60s limit as 0.0h."""
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.0f}min"
    return f"{seconds:.0f}s"


def probe_duration(fp: str | Path) -> float | None:
    """Duration in seconds from the container header, without decoding.

    Returns None when ffprobe cannot determine it (some streams genuinely have
    no duration in the header). None is "unknown", not "zero" -- the caller
    decides what to do with it, and must not treat it as a passing check.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(fp),
            ],
            capture_output=True,
            timeout=FFPROBE_TIMEOUT,
            check=True,
        ).stdout.decode(errors="replace").strip()
    except (subprocess.SubprocessError, OSError):
        return None

    try:
        d = float(out)
    except ValueError:
        return None
    # ffprobe reports "N/A" as nan for some containers.
    return d if d == d and d >= 0 else None


def load_audio_bounded(fp: str | Path) -> np.ndarray:
    """whisperx.load_audio, but duration-checked and timeout-bounded.

    Reimplemented rather than wrapped because the upstream version has no
    timeout and buffers the whole decoded stream via capture_output before
    returning -- which is exactly the step that needs bounding.
    """
    duration = probe_duration(fp)

    # Reject a known-too-long file before spending anything on decoding it.
    if duration is not None and duration > MAX_AUDIO_SECONDS:
        raise AudioTooLong(
            f"Audio is {_fmt_duration(duration)}; the maximum is "
            f"{_fmt_duration(MAX_AUDIO_SECONDS)}."
        )

    cmd = [
        "ffmpeg", "-nostdin", "-threads", "0",
        "-i", str(fp),
        # Hard cap the decoded stream. This is the load-bearing guard: it
        # applies even when the probe returned None, and it also covers a
        # header that understates the real duration. Reading one extra second
        # lets the post-check below distinguish "at the limit" from "over it".
        "-t", str(MAX_AUDIO_SECONDS + 1),
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-",
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, check=True, timeout=FFMPEG_TIMEOUT
        ).stdout
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Decoding timed out after {FFMPEG_TIMEOUT}s"
        ) from e
    except subprocess.CalledProcessError as e:
        # Only the tail of stderr: ffmpeg prints its full build configuration
        # on every invocation, which buries the actual error. This goes to the
        # log only -- the handler in main.py maps RuntimeError to the generic
        # client message, so none of it reaches the user.
        tail = e.stderr.decode(errors="replace").strip().splitlines()[-5:]
        raise RuntimeError("Failed to load audio: " + " | ".join(tail)) from e

    audio = np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0

    # Catches the lying-header case: -t truncated the stream, so check what we
    # actually got rather than what the container claimed.
    if len(audio) / SAMPLE_RATE > MAX_AUDIO_SECONDS:
        raise AudioTooLong(
            f"Audio exceeds the maximum of {_fmt_duration(MAX_AUDIO_SECONDS)}."
        )

    return audio

# Initialize WhisperX model (wraps faster-whisper with batched inference + VAD)
wx_model = whisperx.load_model(
    "large-v3",
    device=DEVICE,
    compute_type=COMPUTE_TYPE,
    language=None,  # auto-detect
    asr_options={"initial_prompt": None},
)

# Initialize diarization pipeline (pyannote-audio)
diarize_pipe = DiarizationPipeline(
    token=HF_TOKEN,
    device=torch.device(DEVICE),
)

print("Loaded")


def diarized_transcribe(
    fp: str | Path,
    language: str = "en",
    diarize: bool = True,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    initial_prompt: str = "",
    task: str = "transcribe",
) -> tuple[dict, tuple[float, float]]:
    audio = load_audio_bounded(fp)

    # 1. Transcribe (batched, VAD-preprocessed)
    t0 = time.time()
    wx_model.options.initial_prompt = initial_prompt or None
    lang_arg = None if language == "auto" else language
    result = wx_model.transcribe(audio, batch_size=BATCH_SIZE, task=task, language=lang_arg)
    lang = result.get("language", language if language != "auto" else "en")
    transcribe_elapsed = time.time() - t0

    # 2. Align (phoneme-level timestamps via wav2vec2)
    #    Skip if no alignment model exists for the detected language —
    #    segment-level timestamps from Whisper are still usable.
    try:
        align_model, align_meta = whisperx.load_align_model(language_code=lang, device=DEVICE)
        result = whisperx.align(
            result["segments"], align_model, align_meta, audio, device=DEVICE,
        )
        del align_model
        gc.collect()
        torch.cuda.empty_cache()
    except ValueError:
        print(f"No alignment model for language '{lang}', skipping alignment")

    # 3. Optionally diarize and assign speakers
    diarize_elapsed = 0.0
    if diarize:
        t1 = time.time()
        diarize_segments = diarize_pipe(
            audio,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        result = whisperx.assign_word_speakers(diarize_segments, result)
        diarize_elapsed = time.time() - t1

    gc.collect()
    torch.cuda.empty_cache()

    # 4. Convert to the output format expected by the frontend:
    #    { text: str, chunks: [{ timestamp: (start, end), text: str, speaker?: str }] }
    chunks = []
    for seg in result["segments"]:
        chunks.append({
            "timestamp": (seg["start"], seg["end"]),
            "text": seg["text"],
            **({"speaker": seg["speaker"]} if "speaker" in seg else {}),
        })

    output = {
        "text": "".join(c["text"] for c in chunks),
        "chunks": chunks,
    }

    return output, (diarize_elapsed, transcribe_elapsed)


if __name__ == "__main__":
    # Test
    import sys
    if len(sys.argv) > 1:
        print(diarized_transcribe(sys.argv[1]))
