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


## Deployment Instructions

TODO 

Frontend environment variables:
* BACKEND_HOST - public backend URL
* TITLE - optional header title

Backend environment variables:
* HF_TOKEN - huggingface api token


## LLM Disclosure 🤖🧠

The WhisperX integration and additional features in this fork were added using AI agents as pair programmer.
I recognise LLM use is ethically complex and carries a heavy environmental burden. As a non-programmer, I aim to thoughtfully balance the use of LLMs against the value it provides.
