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
    # Verify FFmpeg is available
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

        logger.info(f"Job {job_id}: Rendering {len(scenes)} scenes")
        concat_list = []

        for i, scene in enumerate(scenes):
            logger.info(f"Job {job_id}: Processing scene {i + 1}")

            # Save image
            img_data = base64.b64decode(scene["image_base64"])
            img_path = f"{work_dir}/scene_{i}.png"
            with open(img_path, "wb") as f:
                f.write(img_data)

            # Save audio
            audio_data = base64.b64decode(scene["audio_base64"])
            audio_path = f"{work_dir}/scene_{i}.mp3"
            with open(audio_path, "wb") as f:
                f.write(audio_data)

            # Create video segment
            segment_path = f"{work_dir}/segment_{i}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", img_path,
                "-i", audio_path,
                "-c:v", "libx264",
                "-tune", "stillimage",
                "-c:a", "aac",
                "-b:a", "128k",
                "-pix_fmt", "yuv420p",
                "-r", str(fps),
                "-shortest",
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
                segment_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"FFmpeg error scene {i}: {result.stderr}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": f"FFmpeg failed on scene {i}",
                        "details": result.stderr[-500:]
                    }
                )

            concat_list.append(f"file 'segment_{i}.mp4'")

        # Write concat file
        concat_path = f"{work_dir}/concat.txt"
        with open(concat_path, "w") as f:
            f.write("\n".join(concat_list))

        # Concatenate all segments
        output_path = f"{work_dir}/final.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_path,
            "-c", "copy",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
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
            video_base64 = base64.b64encode(f.read()).decode()

        file_size = os.path.getsize(output_path)
        logger.info(
            f"Job {job_id}: Done. Size: {file_size / 1024:.1f} KB"
        )

        return {
            "video_base64": video_base64,
            "file_size_kb": round(file_size / 1024, 1),
            "scenes_count": len(scenes)
        }

    except Exception as e:
        logger.error(f"Job {job_id}: Error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
