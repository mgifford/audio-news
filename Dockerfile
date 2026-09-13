# CPU Docker Space for the audio-news backend (FastAPI + llama-cpp).
# Installs a PREBUILT llama-cpp-python CPU wheel rather than compiling from source:
# the source build OOM-killed HF's build container (exit 137). This also lets us
# drop the whole compiler toolchain, so the image is smaller and the build faster.
FROM python:3.10-slim

WORKDIR /app

# Only runtime needs: curl fetches the model at build time; certs for TLS.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# --only-binary=llama-cpp-python forces the prebuilt CPU wheel from the abetlen
# index (no compilation, no OOM); if no matching wheel exists pip fails clearly
# instead of falling back to a source build.
RUN pip install --no-cache-dir \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
    --only-binary=llama-cpp-python \
    -r requirements.txt

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
