import gc
import time
from pathlib import Path

import os
import torch
import whisperx
from whisperx.diarize import DiarizationPipeline

DEVICE = "cuda"
COMPUTE_TYPE = "float16"
BATCH_SIZE = 16
HF_TOKEN = os.getenv("HF_TOKEN")

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
    diarize: bool = True,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    initial_prompt: str = "",
    task: str = "transcribe",
) -> tuple[dict, tuple[float, float]]:
    audio = whisperx.load_audio(str(fp))

    # 1. Transcribe (batched, VAD-preprocessed)
    t0 = time.time()
    wx_model.options.initial_prompt = initial_prompt or None
    result = wx_model.transcribe(audio, batch_size=BATCH_SIZE, task=task)
    lang = result.get("language", "en")
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
