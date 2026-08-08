FROM python:3.11-slim

RUN pip install --no-cache-dir fastapi uvicorn

COPY render.py /app/render.py

WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "render:app", "--host", "0.0.0.0", "--port", "8000"]
