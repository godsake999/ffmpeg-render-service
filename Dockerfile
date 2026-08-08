FROM python:3.11-slim

# Install FFmpeg - stable Linux package
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir fastapi uvicorn

# Copy app
COPY render.py /app/render.py

WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "render:app", "--host", "0.0.0.0", "--port", "8000"]
