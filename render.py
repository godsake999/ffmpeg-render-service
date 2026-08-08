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

# Store rendered video paths temporarily
VIDEO_STORE: Dict[str, str] = {}


@app.get("/healthz")
def health():
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True
        )
        version = result.stdout.split('\n')[0]
        return {"status": "ok", "ffmpeg": version}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/render")
async def render_video(data: dict):
    job_id = str(uuid.uuid4())[:8]
    work_dir = f"/tmp/{job_id}"
    os.makedirs(work_dir, exist_ok=True)

    try:
        scenes = data.get("scenes", [])
        width = data.get("width", 480)
        height = data.get("height", 480)
        fps = data.get("fps", 20)

        if not scenes:
            return JSONResponse(
                status_code=400,
                content={"error": "No scenes provided"}
            )

        logger.info(f"Job {job_id}: {len(scenes)} scenes at {width}x{height}")
        num_scenes = len(scenes)

        # Save files ONE AT A TIME
        for i, scene in enumerate(scenes):
            img_data = base64.b64decode(scene["image_base64"])
            with open(f"{work_dir}/img_{i}.png", "wb") as f:
                f.write(img_data)
            del img_data
            scene["image_base64"] = None

            audio_data = base64.b64decode(scene["audio_base64"])
            with open(f"{work_dir}/aud_{i}.mp3", "wb") as f:
                f.write(audio_data)
            del audio_data
            scene["audio_base64"] = None

        # Clear input
        scenes = None
        data = None
        gc.collect()

        # Get audio durations
        durations = []
        for i in range(num_scenes):
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                 f"{work_dir}/aud_{i}.mp3"],
                capture_output=True, text=True
            )
            try:
                duration = float(result.stdout.strip())
            except:
                duration = 5.0
            durations.append(duration)

        logger.info(f"Job {job_id}: durations {durations}")

        # Encode segments
        for i in range(num_scenes):
            segment_path = f"{work_dir}/seg_{i}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-loglevel", "error",
                "-loop", "1",
                "-i", f"{work_dir}/img_{i}.png",
                "-i", f"{work_dir}/aud_{i}.mp3",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "stillimage",
                "-c:a", "aac",
                "-b:a", "64k",
                "-pix_fmt", "yuv420p",
                "-r", str(fps),
                "-t", str(durations[i]),
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
                "-threads", "1",
                segment_path
            ]

            logger.info(f"Job {job_id}: encoding {i + 1}/{num_scenes}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)

            if result.returncode != 0:
                logger.error(f"FFmpeg scene {i}: {result.stderr}")
                return JSONResponse(
                    status_code=500,
                    content={"error": f"FFmpeg failed scene {i + 1}"}
                )

            # Delete source files immediately
            try:
                os.remove(f"{work_dir}/img_{i}.png")
                os.remove(f"{work_dir}/aud_{i}.mp3")
            except:
                pass

            gc.collect()

        # Concat file
        concat_path = f"{work_dir}/concat.txt"
        with open(concat_path, "w") as f:
            for i in range(num_scenes):
                f.write(f"file 'seg_{i}.mp4'\n")

        # Concatenate
        output_path = f"{work_dir}/final.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_path,
            "-c", "copy",
            output_path
        ]

        logger.info(f"Job {job_id}: concatenating")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return JSONResponse(
                status_code=500,
                content={"error": "Concat failed"}
            )

        # Delete segments
        for i in range(num_scenes):
            try:
                os.remove(f"{work_dir}/seg_{i}.mp4")
            except:
                pass

        file_size_kb = round(os.path.getsize(output_path) / 1024, 1)
        logger.info(f"Job {job_id}: done, {file_size_kb} KB")

        # Store path for later download
        VIDEO_STORE[job_id] = output_path

        # Return download URL instead of base64
        return {
            "job_id": job_id,
            "download_url": f"/download/{job_id}",
            "file_size_kb": file_size_kb,
            "scenes_count": num_scenes
        }

    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.error(f"Job {job_id}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/download/{job_id}")
async def download_video(job_id: str, background_tasks: BackgroundTasks):
    video_path = VIDEO_STORE.get(job_id)

    if not video_path or not os.path.exists(video_path):
        return JSONResponse(
            status_code=404,
            content={"error": "Video not found or expired"}
        )

    # Clean up after download
    work_dir = os.path.dirname(video_path)
    
    def cleanup():
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
            VIDEO_STORE.pop(job_id, None)
            logger.info(f"Cleaned up {job_id}")
        except:
            pass

    background_tasks.add_task(cleanup)

    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename="video.mp4"
    )
