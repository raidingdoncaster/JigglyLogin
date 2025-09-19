# Use Python
FROM python:3.12-slim

# Install system deps (Tesseract OCR + build tools)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working dir
WORKDIR /app

# Copy project files
COPY . .

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Expose Flask port
EXPOSE 8080

# Run app
CMD ["python", "app.py"]