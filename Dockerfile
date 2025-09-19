# Use official Python base image
FROM python:3.12-slim

# Install system dependencies (Tesseract OCR + build tools)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency file first (better build caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source code
COPY . .

# Expose port for Cloud Run
EXPOSE 8080

# Use Gunicorn to run Flask in production
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]