# Fortera installation guide

## Official project links
- Product simulator: https://lunaradrone.lovable.app
- Lovable editor: https://lovable.dev/projects/5ac1f57c-1ff1-4a5d-8f06-e8897738a0f2
- GitHub source and bridge: https://github.com/b19ua/Fortera
- Hugging Face model: https://huggingface.co/Askit87/Fortera
- PX4 docs: https://docs.px4.io/main/en/
- PX4 source: https://github.com/PX4/PX4-Autopilot
- QGroundControl: https://qgroundcontrol.com/
- MAVSDK Python: https://mavsdk.mavlink.io/main/en/python/
- llama.cpp: https://github.com/ggml-org/llama.cpp

## Supported starting environment
Use 64-bit Ubuntu 24.04 or 22.04 on an x86_64 workstation for the first SITL integration. PX4's current Ubuntu guide supports these LTS releases. Raspberry Pi 5 with 16 GB RAM, NVMe, active cooling and adequate power is the preferred later edge target, but physical Pi performance has not yet been benchmarked.

## 1. Clone the repository
    sudo apt update
    sudo apt install -y git python3 python3-venv python3-pip build-essential
    git clone https://github.com/b19ua/Fortera.git
    cd Fortera

## 2. Download the model
Install Hugging Face CLI:
    python3 -m pip install --user -U "huggingface_hub[cli]"

Download the public model package:
    hf download Askit87/Fortera --local-dir ./hf-model

Locate the GGUF:
    find ./hf-model -name 'Fortera-Q4_K_M.gguf' -print

Copy it to repository root (the published package currently stores it under gguf/):
    cp ./hf-model/gguf/Fortera-Q4_K_M.gguf ./Fortera-Q4_K_M.gguf

Create a tokenizer directory and copy the complete tokenizer file set from the downloaded repository into it. Check the actual file listing on Hugging Face; do not create empty placeholder files:
    mkdir -p tokenizer
    cp ./hf-model/tokenizer.json ./tokenizer/ 2>/dev/null || true
    cp ./hf-model/tokenizer_config.json ./tokenizer/ 2>/dev/null || true
    cp ./hf-model/config.json ./tokenizer/ 2>/dev/null || true
    cp ./hf-model/vocab.json ./tokenizer/ 2>/dev/null || true
    cp ./hf-model/merges.txt ./tokenizer/ 2>/dev/null || true

If the tokenizer files are located in another folder, copy the entire tokenizer set from that folder. Keep model and tokenizer from the same release.

## 3. Install Python dependencies
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install -r bridge/requirements.txt

The optional PX4 backend uses a MAVSDK API matching the legacy System/plugin interface in bridge/backends.py. The reference documentation specifies mavsdk<4 or the matching mavsdk-grpc/server package; do not assume MAVSDK 4.x is a drop-in replacement:
    python -m pip install 'mavsdk<4'

## 4. Install llama.cpp and run Fortera
Install/build llama.cpp for the target OS and architecture. Ensure llama-server is on PATH:
    llama-server --version

Start local inference:
    export FORTERA_MODEL_PATH="$PWD/Fortera-Q4_K_M.gguf"
    export FORTERA_TOKENIZER_PATH="$PWD/tokenizer"
    bash scripts/run_fortera.sh

The server binds to 127.0.0.1:8081. Keep it on loopback. In another terminal:
    source .venv/bin/activate
    python -m fortera_runtime.client "Hold position" --mode simulator --language en

This is inference-only and does not control a vehicle.

## 5. Start the Companion Bridge in bench mode
    source .venv/bin/activate
    export FORTERA_BRIDGE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    export FORTERA_ALLOWED_ORIGINS="http://localhost:8080"
    export FORTERA_BACKEND=bench
    export FORTERA_BENCH_COUNT=1
    python bridge/gateway.py

The default gateway binds to 127.0.0.1:8765. Bench mode is synthetic and does not connect to PX4. Keep the token private: never commit it, place it in a URL, or paste it into screenshots or public chats.

## 6. Run software tests
    python -m unittest discover -s tests -v
    python -m unittest discover -s bridge -p 'test_*.py' -v

Tests cover software validation, not model quality, physical hardware or flight safety.
