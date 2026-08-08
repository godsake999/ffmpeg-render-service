from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
import subprocess
import os
import base64
import uuid
import shutil
import logging
import gc

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
        width = data.get("width", 480)
        height = data.get("height", 480)
        fps = data.get("fps", 20)

        if not scenes:
            return JSONResponse(
                status_code=400,
                content={"error": "No scenes provided"}
            )

        logger.info(f"Job {job_id}: {len(scenes)} scenes at {width}x{height}")

        # Save files ONE AT A TIME and immediately free memory
        for i, scene in enumerate(scenes):
            # Decode and save image
            img_data = base64.b64decode(scene["image_base64"])
            with open(f"{work_dir}/img_{i}.png", "wb") as f:
                f.write(img_data)
            del img_data  # free memory

            # Decode and save audio
            audio_data = base64.b64decode(scene["audio_base64"])
            with open(f"{work_dir}/aud_{i}.mp3", "wb") as f:
                f.write(audio_data)
            del audio_data

            # Clear base64 strings from scene dict
            scene["image_base64"] = None
            scene["audio_base64"] = None

        # Clear input scenes from memory
        scenes = None
        data = None
        gc.collect()

        num_scenes = len([f for f in os.listdir(work_dir) if f.startswith("img_")])

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

        # Encode segments one at a time
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

            logger.info(f"Job {job_id}: encoding scene {i + 1}/{num_scenes}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)

            if result.returncode != 0:
                logger.error(f"FFmpeg error scene {i}: {result.stderr}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": f"FFmpeg failed on scene {i + 1}",
                        "details": result.stderr[-300:]
                    }
                )

            # Delete source files after encoding to save disk
            try:
                os.remove(f"{work_dir}/img_{i}.png")
                os.remove(f"{work_dir}/aud_{i}.mp3")
            except:
                pass

        # Concat file
        concat_path = f"{work_dir}/concat.txt"
        with open(concat_path, "w") as f:
            for i in range(num_scenes):
                f.write(f"file 'seg_{i}.mp4'\n")

        # Concatenate (fast - no re-encoding)
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
            logger.error(f"Concat error: {result.stderr}")
            return JSONResponse(
                status_code=500,
                content={"error": "Concat failed", "details": result.stderr[-300:]}
            )

        # Delete segment files after concat
        for i in range(num_scenes):
            try:
                os.remove(f"{work_dir}/seg_{i}.mp4")
            except:
                pass

        # Read video file
        with open(output_path, "rb") as f:
            video_data = f.read()

        file_size_kb = round(len(video_data) / 1024, 1)
        logger.info(f"Job {job_id}: video size {file_size_kb} KB")

        # Encode to base64
        video_base64 = base64.b64encode(video_data).decode()
        del video_data
        gc.collect()

        return {
            "video_base64": video_base64,
            "file_size_kb": file_size_kb,
            "scenes_count": num_scenes
        }

    except subprocess.TimeoutExpired as e:
        logger.error(f"Job {job_id}: timeout")
        return JSONResponse(
            status_code=500,
            content={"error": "Rendering timeout"}
        )
    except Exception as e:
        logger.error(f"Job {job_id}: error {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        gc.collect()
