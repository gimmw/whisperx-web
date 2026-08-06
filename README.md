# WhisperX Web 

Basic Drop-and-Transcribe web UI and backend for WhisperX transcription.

Fork of [one-among-us/whisper-web](https://github.com/one-among-us/whisper-web) swapping `faster-whisper` for [WhisperX](https://github.com/m-bain/whisperX)

![Frontend Upload page](screenshot1.png)

## Features

* Speech-to-text with timestamps
* Speaker diarisation


## Multi-user model

This service does not use accounts or passwords. Each uploaded file is assigned a random, unguessable link (UUID) that is used to check progress and download the transcript — Anyone who has that link can access the results, so it should be treated like a private URL and not shared. 

There is no way for other users to browse, list, or guess another user's files under normal circumstances, and the original audio file is never exposed for download; only the transcript is. 

Uploads are processed one at a time in a first-in, first-out queue — If you upload while another job is running, your file waits its turn, and the progress page shows your position in the queue along with live CPU/GPU usage once your file starts processing. 


## Deployment

Two containers: a frontend (nginx serving the built SPA) and a backend (FastAPI
+ WhisperX, needs an NVIDIA GPU).
 
#### K8s notes
* Proxy backend on `/api` for a same-origin setup; Set backend service in nginx.conf. 
* When using ingress, set Ingress body-size limit to at least `MAX_UPLOAD_MB`, or large uploads
  fail at the Ingress before reaching the app. On ingress-nginx:
  `nginx.ingress.kubernetes.io/proxy-body-size: 512m`.
* Transcription is slow. Raise the Ingress read/send timeouts (e.g.
  `proxy-read-timeout: "3600"`) so long jobs are not cut off.
* The backend needs `nvidia.com/gpu` in its resource limits and writable storage
  at `/ws/tmp-whisper` — use a PVC, since results are lost on pod restart
  otherwise. Uploads are processed one at a time, so run a single replica.


### Environment variables

Frontend:
* `BACKEND_HOST` - backend URL. Defaults to `/api` (proxied to the backend).
  Set an absolute URL only if the backend is exposed separately; that makes
  requests cross-origin, so the backend's `CORS_ORIGINS` must then list this
  frontend's origin.
* `TITLE` - optional header title

Backend:
* `HF_TOKEN` - huggingface api token
* `MAX_UPLOAD_MB` - maximum accepted upload size in MB (default `512`).
* `CORS_ORIGINS` - comma-separated list of browser origins allowed to call the
  API, e.g. `https://whisper.internal.example`. Defaults to `*`. Not needed in
  the default same-origin setup (/api) above.


## LLM Disclosure 🤖🧠

The WhisperX integration and additional features in this fork were added using AI agents as pair programmer.
I recognise LLM use is ethically complex and carries a heavy environmental burden. As a non-programmer, I aim to thoughtfully balance the use of LLMs against the value it provides.
