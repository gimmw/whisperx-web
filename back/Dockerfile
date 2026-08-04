# Use NVIDIA CUDA runtime as base (WhisperX requires CUDA 12.8+)
FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Install system dependencies
# 1. Add deadsnakes PPA for Python 3.12
# 2. Install Python 3.12 and other required system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    ca-certificates \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    ffmpeg \
    libmagic1 \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv/bin/uv
ENV PATH="/uv/bin:$PATH"

# Create an unprivileged user to run the application.
# A fixed, high, non-zero UID/GID is used so that it can be asserted from the
# Kubernetes pod security context (runAsUser/runAsNonRoot) and so that any
# volume ownership is predictable across rebuilds.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} app && \
    useradd --uid ${APP_UID} --gid ${APP_GID} --create-home --shell /usr/sbin/nologin app

# Set working directory
WORKDIR /app

# Copy dependency files first for better caching
COPY --chown=${APP_UID}:${APP_GID} pyproject.toml uv.lock ./

# Install dependencies into a virtual environment
# We use /app/.venv and add it to PATH
RUN uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv sync --frozen --no-cache

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Copy the application code
COPY --chown=${APP_UID}:${APP_GID} . .

# Create the data and cache directories.
# /ws/tmp-whisper is a volume mount point (see compose.yaml) and /tmp/whisper is
# created at import time by main.py; both must be writable by the app user.
# HF_HOME/TORCH_HOME are pointed at the app user's home because whisperx and
# pyannote download model weights on first run -- left at the default they would
# resolve to /root/.cache and fail for a non-root user.
ENV HF_HOME=/home/app/.cache/huggingface \
    TORCH_HOME=/home/app/.cache/torch \
    XDG_CACHE_HOME=/home/app/.cache \
    MPLCONFIGDIR=/home/app/.cache/matplotlib
# Note: /app is deliberately NOT chown'd. The venv is ~7GB and recursively
# chowning it would duplicate every file into a new image layer. The app only
# needs to read and execute it, and root-owned code that the runtime user cannot
# modify is the safer arrangement anyway.
RUN mkdir -p /ws/tmp-whisper/audio /ws/tmp-whisper/transcription \
    /tmp/whisper \
    ${HF_HOME} ${TORCH_HOME} ${MPLCONFIGDIR} && \
    chown -R ${APP_UID}:${APP_GID} /ws/tmp-whisper /tmp/whisper /home/app/.cache

# Drop privileges -- everything below this line runs as the unprivileged user
USER ${APP_UID}:${APP_GID}

# Expose the port from main.py (49585 is unprivileged, so no CAP_NET_BIND_SERVICE needed)
EXPOSE 49585

# Run the application
CMD ["python", "main.py"]
