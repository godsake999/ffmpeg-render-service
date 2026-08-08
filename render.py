from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
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
    return {"status": "ok", "service": "File Host for Creatomate"}


@app.post("/upload-scenes")
async def upload_scenes(data: dict):
    """
    Upload all scene images and audio at once.
    Returns public URLs that Creatomate can access.
    """
    batch_id = str(uuid.uuid4())[:8]

    try:
        scenes = data.get("scenes", [])
        base_url = data.get("base_url", "")

        if not scenes:
            return JSONResponse(status_code=400, content={"error": "No scenes provided"})

        if not base_url:
            return JSONResponse(status_code=400, content={"error": "base_url required"})

        results = []

        for i, scene in enumerate(scenes):
            # Save image
            img_id = f"{batch_id}_img_{i}"
            img_dir = f"/tmp/uploads/{img_id}"
            os.makedirs(img_dir, exist_ok=True)
            img_path = f"{img_dir}/file.png"

            with open(img_path, "wb") as f:
                f.write(base64.b64decode(scene["image_base64"]))

            FILE_STORE[img_id] = img_path

            # Save audio
            aud_id = f"{batch_id}_aud_{i}"
            aud_dir = f"/tmp/uploads/{aud_id}"
            os.makedirs(aud_dir, exist_ok=True)
            aud_path = f"{aud_dir}/file.mp3"

            with open(aud_path, "wb") as f:
                f.write(base64.b64decode(scene["audio_base64"]))

            FILE_STORE[aud_id] = aud_path

            results.append({
                "scene_number": i + 1,
                "image_url": f"{base_url}/file/{img_id}.png",
                "audio_url": f"{base_url}/file/{aud_id}.mp3"
            })

            # Free memory
            scene["image_base64"] = None
            scene["audio_base64"] = None
            gc.collect()

        logger.info(f"Batch {batch_id}: uploaded {len(scenes)} scenes")

        return {
            "batch_id": batch_id,
            "scenes": results,
            "expires_in_minutes": 30
        }

    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/file/{filename}")
async def get_file(filename: str):
    """Serve uploaded file to Creatomate"""
    file_id = filename.rsplit('.', 1)[0]
    file_path = FILE_STORE.get(file_id)

    if not file_path or not os.path.exists(file_path):
        return JSONResponse(status_code=404, content={"error": "File not found"})

    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
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


@app.post("/cleanup/{batch_id}")
async def cleanup_batch(batch_id: str):
    """Delete all files for a batch after video is rendered"""
    deleted = 0
    keys_to_remove = [k for k in FILE_STORE.keys() if k.startswith(batch_id)]

    for key in keys_to_remove:
        file_path = FILE_STORE.pop(key, None)
        if file_path:
            work_dir = os.path.dirname(file_path)
            shutil.rmtree(work_dir, ignore_errors=True)
            deleted += 1

    return {"deleted": deleted, "batch_id": batch_id}
