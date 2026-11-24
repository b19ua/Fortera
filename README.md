# Fortera by AskIt AI

**Multilingual local command-understanding model for drone companion-computer research.** Fortera converts operator instructions into constrained JSON proposals for an independent validation layer. It is a research prototype, not an autopilot or certified flight-control system.

## Model
- Hugging Face: https://huggingface.co/Askit87/Fortera
- Base: `Qwen/Qwen3-1.7B`; supervised LoRA r=16, alpha=32, dropout=0.05, merged into base weights.
- Languages: English, Russian, Arabic, German.
- Edge artifact: GGUF Q4_K_M (~1.03 GiB), hosted on Hugging Face rather than duplicated in this Git repository.
- Local runtime: `llama.cpp` raw `/completion` endpoint; Python client uses the matching tokenizer prompt format.

## Repository layout
- `fortera_runtime/` — local inference client and fail-closed proposal validator.
- `tests/` — validator unit tests.
- `scripts/` — local server launcher.
- `model_manifest.json` — release metadata and artifact checksum.

Download the GGUF and tokenizer from the [Hugging Face model repository](https://huggingface.co/Askit87/Fortera).

## Quick start
1. Install `llama.cpp` and make `llama-server` available on PATH.
2. Download `Fortera-Q4_K_M.gguf` and tokenizer files from Hugging Face.
3. Install Python dependencies: `python -m pip install -r requirements.txt`.
4. Start the server: `bash scripts/run_fortera.sh` (set `FORTERA_MODEL_PATH` if the model is elsewhere).
5. In another terminal run: `python -m fortera_runtime.client "Hold position" --mode simulator --language en`.

The server binds to `127.0.0.1:8081`. Keep it on loopback. The client returns a validated proposal with `dispatch_enabled=false`; it never sends commands to a drone.

## Evaluation
On 108 held-out examples using the matching raw-prompt path: 108/108 parseable JSON; 103/108 exact command matches before compatibility normalization. Five differences were simulator `hold` versus expected `hover`; the packaged simulator-only normalization maps that alias, yielding 108/108 normalized matches. This is a small internal evaluation on a RunPod x86_64 CPU host, not an independent benchmark. No Raspberry Pi, PX4 SITL, HIL, or physical flight validation is claimed.

Training data: 396 train + 108 validation synthetic/template-authored examples, four languages. Not real flight telemetry.

## Safety
Never connect raw model output directly to MAVLink/MAVSDK, motors, actuators, or a flight controller. The validator is a narrow schema/parameter gate, not a complete flight-safety policy. A downstream system must independently enforce operator authorization, battery/vehicle health, telemetry freshness, GPS/home, flight mode, obstacle sensing, geofence, waypoint permissions, lost-link behavior, and autopilot failsafes. Physical dispatch must remain disabled until separately engineered PX4 SITL, HIL, and supervised hardware testing are completed.

## License and provenance
The **model weights** are a fine-tuned derivative of `Qwen/Qwen3-1.7B`, whose upstream license is Apache-2.0. The full upstream license and attribution notices are included in the [Hugging Face model repository](https://huggingface.co/Askit87/Fortera). The runtime source code in this GitHub repository does not yet have a separately declared license; do not assume the model-weights license automatically applies to every source file here. See [LICENSE_SCOPE.md](LICENSE_SCOPE.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**Published by AskIt AI.**