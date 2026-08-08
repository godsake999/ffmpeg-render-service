from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
import subprocess
import os
import base64
import uuid
import shutil
import logging
import gc
from typing import Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

FILE_STORE: Dict[str, str] = {}


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload_file(data: dict):
    """Upload base64 file, return public URL"""
    file_id = str(uuid.uuid4())[:12]
    work_dir = f"/tmp/uploads/{file_id}"
    os.makedirs(work_dir, exist_ok=True)

    try:
        file_base64 = data.get("file_base64")
        file_extension = data.get("extension", "bin")

        if not file_base64:
            return JSONResponse(status_code=400, content={"error": "No file_base64"})

        file_path = f"{work_dir}/file.{file_extension}"
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(file_base64))

        FILE_STORE[file_id] = file_path

        file_size_kb = round(os.path.getsize(file_path) / 1024, 1)
        logger.info(f"Uploaded {file_id}: {file_size_kb} KB")

        return {
            "file_id": file_id,
            "url": f"/file/{file_id}.{file_extension}",
            "size_kb": file_size_kb
        }

    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/file/{filename}")
async def get_file(filename: str):
    """Serve uploaded file"""
    file_id = filename.split('.')[0]
    file_path = FILE_STORE.get(file_id)

    if not file_path or not os.path.exists(file_path):
        return JSONResponse(status_code=404, content={"error": "File not found"})

    extension = filename.split('.')[-1].lower()
    mime_types = {
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'mp3': 'audio/mpeg',
        'mp4': 'video/mp4',
        'wav': 'audio/wav'
    }
    media_type = mime_types.get(extension, 'application/octet-stream')

    return FileResponse(file_path, media_type=media_type)


@app.delete("/file/{file_id}")
async def delete_file(file_id: str):
    """Cleanup file"""
    file_path = FILE_STORE.pop(file_id, None)
    if file_path:
        work_dir = os.path.dirname(file_path)
        shutil.rmtree(work_dir, ignore_errors=True)
    return {"deleted": True}
