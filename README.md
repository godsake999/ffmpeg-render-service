# FFmpeg Render Service

Combines images + audio into video using FFmpeg.

## API

POST /render
- Input: JSON with scenes array (base64 images + audio)
- Output: base64 encoded MP4 video

GET /healthz
- Health check
