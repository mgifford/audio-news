# CPU-optimized Docker Space for the audio-news Phase 2 backend (FastAPI + llama-cpp).
FROM python:3.10-slim

WORKDIR /app

# Build dependencies for compiling llama-cpp-python with OpenBLAS.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    pkg-config \
    git \
    curl \
    ca-certificates \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# Build llama-cpp-python against OpenBLAS for faster CPU inference.
ENV CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Model is configurable so the image can be rebuilt for a different GGUF without
# editing the Dockerfile. Default is Qwen2.5-1.5B-Instruct (Apache-2.0).
# NOTE: confirm the exact filename exists in the source repo before building.
ARG MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"

# --fail makes a 404/HTML error abort the build instead of silently saving an
# error page as model.gguf (which would crash the model load at runtime).
RUN mkdir -p /app/models && \
    curl -fL -o /app/models/model.gguf "${MODEL_URL}"

COPY . .

# Hugging Face Spaces route to this port (see README front matter app_port).
EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
