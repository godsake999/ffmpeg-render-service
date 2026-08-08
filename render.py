from fastapi import FastAPI
from fastapi.responses import JSONResponse
import subprocess
import os
import base64
import uuid
import shutil
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


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
        width = data.get("width", 720)
        height = data.get("height", 720)
        fps = data.get("fps", 24)

        if not scenes:
            return JSONResponse(
                status_code=400,
                content={"error": "No scenes provided"}
            )

        logger.info(f"Job {job_id}: Rendering {len(scenes)} scenes at {width}x{height}")

        # Save all files first
        for i, scene in enumerate(scenes):
            img_data = base64.b64decode(scene["image_base64"])
            with open(f"{work_dir}/img_{i}.png", "wb") as f:
                f.write(img_data)

            audio_data = base64.b64decode(scene["audio_base64"])
            with open(f"{work_dir}/aud_{i}.mp3", "wb") as f:
                f.write(audio_data)

        # Get duration of each audio for proper image duration
        durations = []
        for i in range(len(scenes)):
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

        logger.info(f"Job {job_id}: Audio durations: {durations}")

        # Create segments with fast preset
        for i in range(len(scenes)):
            segment_path = f"{work_dir}/seg_{i}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-loglevel", "error",
                "-loop", "1",
                "-i", f"{work_dir}/img_{i}.png",
                "-i", f"{work_dir}/aud_{i}.mp3",
                "-c:v", "libx264",
                "-preset", "ultrafast",   # FASTEST encoding
                "-tune", "stillimage",
                "-c:a", "aac",
                "-b:a", "96k",
                "-pix_fmt", "yuv420p",
                "-r", str(fps),
                "-t", str(durations[i]),
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
                segment_path
            ]

            logger.info(f"Job {job_id}: Encoding scene {i + 1}/{len(scenes)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                logger.error(f"FFmpeg error scene {i}: {result.stderr}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": f"FFmpeg failed on scene {i + 1}",
                        "details": result.stderr[-500:]
                    }
                )

        # Concat file
        concat_path = f"{work_dir}/concat.txt"
        with open(concat_path, "w") as f:
            for i in range(len(scenes)):
                f.write(f"file 'seg_{i}.mp4'\n")

        # Concatenate all segments (fast, no re-encoding)
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

        logger.info(f"Job {job_id}: Concatenating {len(scenes)} segments")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            logger.error(f"Concat error: {result.stderr}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Concat failed",
                    "details": result.stderr[-500:]
                }
            )

        # Read final video
        with open(output_path, "rb") as f:
            video_data = f.read()

        video_base64 = base64.b64encode(video_data).decode()
        file_size_kb = round(len(video_data) / 1024, 1)

        logger.info(f"Job {job_id}: Done. {file_size_kb} KB")

        return {
            "video_base64": video_base64,
            "file_size_kb": file_size_kb,
            "scenes_count": len(scenes)
        }

    except subprocess.TimeoutExpired as e:
        logger.error(f"Job {job_id}: Timeout - {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Rendering timeout: {str(e)}"}
        )
    except Exception as e:
        logger.error(f"Job {job_id}: Error - {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
