# =============================================================================
# Stage 1: Build Rust native extension (libriichi.so)
# =============================================================================
FROM rust:1.80-slim AS builder

WORKDIR /build
COPY vendor/Mortal ./vendor/Mortal

WORKDIR /build/vendor/Mortal
RUN cargo build -p libriichi --release

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

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Copy compiled libriichi.so from builder stage into server/engine/
COPY --from=builder /build/vendor/Mortal/target/release/libriichi.so /app/server/engine/libriichi.so

# Ensure data directories exist
RUN mkdir -p data/models data/records data/cache

# Download default Mortal model weights from Hugging Face if not provided
RUN if [ ! -f data/models/mortal.pth ]; then \
      curl -L -o data/models/mortal.pth https://huggingface.co/VoidShine/mortal-298k/resolve/main/mortal_298k.pth; \
    fi

ENV HOST=0.0.0.0
ENV PORT=7860
EXPOSE 7860

CMD ["python", "run.py", "--no-browser"]
