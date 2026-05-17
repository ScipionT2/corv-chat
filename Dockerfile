# Nova — Docker Image
#
# NOTE: Audio input/output (microphone, speaker) does NOT work inside Docker.
# This image is intended for API-mode use, testing, or development.
# The health/status HTTP endpoint works normally.
#
# Build:
#   docker build -t nova .
#
# Run (API/health mode):
#   docker run -p 8765:8765 --env-file .env nova

FROM python:3.12-slim AS base

# System dependencies for audio libraries (build-time only for C extensions)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        portaudio19-dev \
        libsndfile1 \
        build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose health/status port
EXPOSE 8765

# Default: run Nova (will fail on audio without host devices)
# Override with your own command for API-mode or testing
ENTRYPOINT ["python", "main.py"]
CMD ["--tts", "say", "--log-level", "INFO"]
