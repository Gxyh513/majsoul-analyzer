# =============================================================================
# Stage 1: Build Rust native extension (libriichi.so) with Python 3.12 headers
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
ENV PATH="/root/.cargo/bin:${PATH}"

RUN git init ./vendor/Mortal && \
    git -C ./vendor/Mortal remote add origin https://github.com/Equim-chan/Mortal.git && \
    git -C ./vendor/Mortal fetch --depth 1 origin 0cff2b52982be5b1163aa9a62fb01f03ce91e0d2 && \
    git -C ./vendor/Mortal checkout --detach FETCH_HEAD

WORKDIR /build/vendor/Mortal
RUN cargo build -p libriichi --lib --release

# =============================================================================
# Stage 2: Production Python 3.12 Runtime
# =============================================================================
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (curl for model downloading, git for private modules)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install CPU ONNX Runtime and the application dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Copy compiled libriichi.so from builder stage into server/engine/
RUN mkdir -p /app/server/engine
COPY --from=builder /build/vendor/Mortal/target/release/libriichi.so /app/server/engine/libriichi.so

# Ensure data directories exist
RUN mkdir -p data/models data/records data/cache

# Download and verify the fixed S42 ONNX artifact.
RUN curl -fSL --retry 3 -o data/models/model.onnx \
    https://huggingface.co/xzhe121/Mortal-S42-onnx/resolve/2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a/model.onnx && \
    echo "30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4  data/models/model.onnx" | sha256sum -c - && \
    python -c "from server.engine.onnx_mortal import OnnxMortal; OnnxMortal('data/models/model.onnx')"

ENV HOST=0.0.0.0
ENV PORT=7860
EXPOSE 7860

CMD ["python", "run.py", "--host", "0.0.0.0", "--no-browser"]
