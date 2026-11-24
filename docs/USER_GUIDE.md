# Fortera user and operator guide

## Project map
| Component | Purpose | Link |
|---|---|---|
| Fortera web demo | Browser 3D city simulator and conversational interface | https://lunaradrone.lovable.app |
| Lovable editor | Edit/deploy the web application | https://lovable.dev/projects/5ac1f57c-1ff1-4a5d-8f06-e8897738a0f2 |
| GitHub | Source, Companion Bridge, runtime, tests, instructions | https://github.com/b19ua/Fortera |
| Hugging Face | Model weights, GGUF, tokenizer and model card | https://huggingface.co/Askit87/Fortera |
| PX4 | Autopilot source and documentation | https://github.com/PX4/PX4-Autopilot / https://docs.px4.io/main/en/ |
| QGroundControl | Ground station for SITL and later qualified testing | https://qgroundcontrol.com/ |
| MAVSDK | Python API for MAVLink autopilots | https://mavsdk.mavlink.io/main/en/python/ |

## Three different operating environments
1. **Browser simulator:** interactive 3D city and conversational experience in the web app. Its vehicle state is simulated in the browser.
2. **Companion Bridge bench:** external Python process with a synthetic backend. It tests pairing, protocol, telemetry and validation without an autopilot.
3. **PX4 SITL:** PX4 flight stack plus Gazebo virtual vehicle. This tests the companion integration against a software autopilot.
4. **Physical aircraft:** not validated by the current release. Do not treat any of the three software environments as evidence of flight safety.

## Start the local model
Follow INSTALLATION.md. Start llama.cpp with Fortera GGUF and matching tokenizer. The local inference endpoint is 127.0.0.1:8081.

## Generate a command proposal
From the repository root:
    python -m companion.fortera_bridge "Take off to 10 metres" --vehicle F01 --language en

Supported language flags: en, ru, ar, de. Default behaviour is proposal-only. It prints a Bridge v2 JSON envelope and does not send it.

## Fortera-to-Bridge command mapping
| Model proposal | Bridge v2 action | Constraint |
|---|---|---|
| takeoff with value | takeoff with altitude_m | 3–30 m |
| goto with waypoint_id | goto with same ID | ID must be configured in gateway |
| hold | hold | preempts a pending request |
| hover | hold | conservative mapping |
| anything else | rejected | no guessing or fallback |

The adapter does not support arm/disarm, return-home, land, camera control, obstacle avoidance, follow, orbit or multi-drone planning. The model cannot invent GPS coordinates.

## Operating rules
- Keep llama-server and the gateway on loopback unless a reviewed TLS deployment is configured.
- Never commit tokens, certificates, API keys or .env files.
- Keep the physical hardware interlock disabled except for a controlled local SITL session.
- Arming/disarming remains with the local operator.
- Configure goto waypoints on the companion before requesting goto.
- Read current telemetry after every command.
- A backend acknowledgement is not proof of arrival or mission completion.
- On timeout, inspect actual telemetry before retrying.
- If vehicle state is unclear, stop sending new commands and use independent pilot/ground-station controls.

## Raspberry Pi deployment outline
1. Install a 64-bit Raspberry Pi OS or supported 64-bit Ubuntu.
2. Mount NVMe storage; provide active cooling and adequate power.
3. Clone this repository and follow INSTALLATION.md.
4. Install an ARM64 llama.cpp build and verify llama-server starts.
5. Download the GGUF and tokenizer from Hugging Face onto NVMe.
6. Start in proposal-only mode and run tests.
7. Complete PX4 SITL validation on a workstation before connecting a physical autopilot.
8. For hardware deployment, create a separate reviewed configuration, onboard safety policy and physical test plan. Never reuse the SITL flags.

The GGUF was evaluated on an x86_64 CPU host. Raspberry Pi throughput, temperature, memory pressure and latency remain to be measured.

## Update source and model
    git pull --ff-only
    source .venv/bin/activate
    python -m unittest discover -s tests -v
    python -m unittest discover -s bridge -p 'test_*.py' -v

For model updates, download a pinned model release, verify its SHA256 manifest, and update the GGUF and tokenizer together. Do not hot-swap a model while a mission is active.

See PX4_SITL.md, ARCHITECTURE.md, BRIDGE_PROTOCOL.md and TROUBLESHOOTING.md for detailed procedures.
