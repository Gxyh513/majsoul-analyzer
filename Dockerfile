# =============================================================================
# Stage 1: Build Rust native extension (libriichi.so)
# =============================================================================
FROM rust:latest AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*

COPY vendor/Mortal ./vendor/Mortal

RUN if [ ! -f ./vendor/Mortal/Cargo.toml ]; then \
      rm -rf ./vendor/Mortal && \
      git clone --depth 1 https://github.com/Equim-chan/Mortal.git ./vendor/Mortal; \
    fi

WORKDIR /build/vendor/Mortal
RUN cargo build -p libriichi --lib --release

# =============================================================================
# Stage 2: Production Python Runtime
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (curl for model downloading)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install lightweight CPU-optimized PyTorch first (~180MB instead of 2.8GB CUDA), then requirements
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Copy compiled libriichi.so from builder stage into server/engine/
RUN mkdir -p /app/server/engine
COPY --from=builder /build/vendor/Mortal/target/release/libriichi.so /app/server/engine/libriichi.so

# Ensure data directories exist
RUN mkdir -p data/models data/records data/cache

# Download default Mortal (v3) lightweight model weights if not provided (with hf-mirror fallback)
RUN if [ ! -f data/models/mortal.pth ]; then \
      curl -L -o data/models/mortal.pth https://hf-mirror.com/VoidShine/mortal-298k/resolve/main/mortal_298k.pth || \
      curl -L -o data/models/mortal.pth https://huggingface.co/VoidShine/mortal-298k/resolve/main/mortal_298k.pth; \
    fi

ENV HOST=0.0.0.0
ENV PORT=7860
EXPOSE 7860

CMD ["python", "run.py", "--host", "0.0.0.0", "--no-browser"]
