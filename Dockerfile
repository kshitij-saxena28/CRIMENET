FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin tesseract-ocr-mar \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Run as an unprivileged user; /data holds the database and evidence (mounted as a volume).
RUN useradd --system --uid 10001 --home /app app && mkdir -p /data/evidence && chown -R app:app /data /app
USER app
ENV DATABASE_URL=sqlite:////data/darkcrimenet.db EVIDENCE_DIR=/data/evidence
EXPOSE 8000
CMD ["uvicorn","backend.app.main:app","--host","0.0.0.0","--port","8000"]
