# Fortera by AskIt AI

**Multilingual local command-understanding model for drone companion-computer research.** Fortera converts operator instructions into constrained JSON proposals for an independent validation layer. It is a research prototype, not an autopilot or certified flight-control system.

## Start here
- [Complete installation guide](docs/INSTALLATION.md)
- [Operator/user manual](docs/USER_GUIDE.md)
- [PX4 SITL integration procedure](docs/PX4_SITL.md)
- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Companion Bridge v2 protocol](docs/BRIDGE_PROTOCOL.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

## Official project links
- Web simulator: https://lunaradrone.lovable.app
- Lovable editor: https://lovable.dev/projects/5ac1f57c-1ff1-4a5d-8f06-e8897738a0f2
- Model, GGUF and tokenizer: https://huggingface.co/Askit87/Fortera
- PX4: https://docs.px4.io/main/en/
- QGroundControl: https://qgroundcontrol.com/
- MAVSDK Python: https://mavsdk.mavlink.io/main/en/python/
- llama.cpp: https://github.com/ggml-org/llama.cpp

## Model
- Base: Qwen/Qwen3-1.7B; supervised LoRA r=16, alpha=32, dropout=0.05, merged into base weights.
- Languages: English, Russian, Arabic, German.
- Edge artifact: GGUF Q4_K_M (~1.03 GiB), hosted on Hugging Face, not duplicated in GitHub.
- Local runtime: llama.cpp raw /completion endpoint; Python client uses the matching tokenizer prompt format.
- Evaluation on 108 held-out examples: 108/108 parseable JSON; 103/108 exact outputs before simulator-only hold-to-hover compatibility normalization. Internal CPU test only.

## Repository layout
- bridge/ — authenticated WebSocket companion gateway, bench/MAVSDK/ROS2 backends, policy and obstacle telemetry.
- companion/ — Fortera proposal-to-Bridge v2 adapter; default proposal-only.
- fortera_runtime/ — local inference client and fail-closed validator.
- tests/ and bridge/ — unit tests.
- docs/ — complete setup, operation, PX4 SITL, architecture, protocol and troubleshooting guides.
- scripts/ — local llama.cpp launcher.
- model_manifest.json — model release metadata and artifact checksum.

## Quick start
Follow [Installation](docs/INSTALLATION.md). In brief:
1. Clone this repository.
2. Download Fortera GGUF and tokenizer from Hugging Face.
3. Install llama.cpp and Python dependencies.
4. Start llama-server on 127.0.0.1:8081.
5. Start the bridge in bench mode first.
6. Run the Fortera adapter without dispatch to inspect a proposal:
   python -m companion.fortera_bridge "Take off to 10 metres" --vehicle F01 --language en

PX4 SITL instructions are separate. The explicit SITL dispatch option requires --dispatch-sitl and --confirm-sitl and local-only endpoint guards.

## Safety status
Never connect raw model output directly to MAVLink/MAVSDK, motors, actuators or a flight controller. The model is untrusted. The adapter translates only takeoff, goto, hold and hover; all other actions are rejected. The gateway independently checks the protocol and policy. The bridge never arms/disarms.

The current integration is **prepared, not yet end-to-end SITL verified**. No physical Raspberry Pi benchmark, PX4 SITL pass, HITL test or real-drone flight result is claimed. No physical dispatch should be enabled using the SITL configuration.

## License
The model weights are a fine-tuned derivative of Qwen/Qwen3-1.7B, whose upstream license is Apache-2.0. Full upstream license and attribution notices are included with the model on Hugging Face. Runtime source code in this repository does not yet have a separately declared license; do not assume the model-weight license applies automatically to every source file. See [license scope](LICENSE_SCOPE.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

**Published by AskIt AI.**
