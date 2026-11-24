# PX4 SITL integration — supervised software simulation

**Status: prepared, not yet end-to-end tested.** The Fortera repository now contains the local model runtime, proposal adapter and Companion Bridge source. No PX4 SITL session has been executed and verified as part of this publication. Complete this procedure on an Ubuntu workstation and record evidence before calling SITL integration complete.

## 1. What SITL means
PX4 Software-in-the-Loop runs the PX4 flight stack on a development computer while a simulator supplies the virtual vehicle and world. It is not physical flight and is different from HITL. PX4 documentation: https://docs.px4.io/main/en/simulation/.

Normal PX4 network defaults are:
- UDP 14540: external developer APIs such as MAVSDK.
- UDP 14550: ground station such as QGroundControl.
Confirm actual ports in the SITL startup output; multi-instance and custom configurations can differ.

## 2. Workstation
Use Ubuntu 24.04 or 22.04 LTS, 64-bit. PX4 current toolchain guide: https://docs.px4.io/main/en/dev_setup/dev_env_linux_ubuntu.

Install PX4:
    git clone https://github.com/PX4/PX4-Autopilot.git --recursive
    bash ./PX4-Autopilot/Tools/setup/ubuntu.sh

Follow installer prompts and restart when requested. The PX4 setup script is intended for a clean supported Ubuntu LTS install. Review the official guide first if the workstation already has a custom ROS/Gazebo toolchain.

## 3. Launch Gazebo X500 SITL
Terminal A:
    cd ~/PX4-Autopilot
    make px4_sitl gz_x500

Wait until PX4 and Gazebo report the virtual vehicle is running. Keep the terminal open.

Install and open QGroundControl: https://qgroundcontrol.com/. Confirm that QGC shows the virtual X500 and inspect health, GPS/home, battery and flight mode. Do not connect a physical flight controller during this SITL-only procedure.

Official Gazebo guide: https://docs.px4.io/main/en/sim_gazebo_gz/.

## 4. Prepare Fortera
In another terminal:
    git clone https://github.com/b19ua/Fortera.git
    cd Fortera
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install -r bridge/requirements.txt
    python -m pip install 'mavsdk<4'

The bridge backend currently uses a legacy MAVSDK System/plugin API. The exact MAVSDK Python package and server must be compatible with that API. MAVSDK 4.x is not assumed to be drop-in compatible; confirm the package version with a direct SITL connection before continuing.

Download model files:
    python3 -m pip install --user -U "huggingface_hub[cli]"
    hf download Askit87/Fortera --local-dir ./hf-model
    cp ./hf-model/gguf/Fortera-Q4_K_M.gguf ./Fortera-Q4_K_M.gguf

Copy the complete tokenizer set from the downloaded model repository into ./tokenizer. Do not mix tokenizer revisions.

## 5. Start local Fortera inference
Terminal B:
    source .venv/bin/activate
    export FORTERA_MODEL_PATH="$PWD/Fortera-Q4_K_M.gguf"
    export FORTERA_TOKENIZER_PATH="$PWD/tokenizer"
    bash scripts/run_fortera.sh

The inference server must listen only on 127.0.0.1:8081.

## 6. Start gateway against local SITL
Terminal C:
    source .venv/bin/activate
    export FORTERA_BRIDGE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    export FORTERA_ALLOWED_ORIGINS="http://localhost:8080"
    export FORTERA_BACKEND=mavsdk
    export FORTERA_MAVSDK_ENDPOINTS='{"F01":"udpin://127.0.0.1:14540"}'
    export FORTERA_BIND=127.0.0.1
    export FORTERA_PORT=8765
    export FORTERA_SITL_MODE=1
    export FORTERA_ENABLE_HARDWARE=1
    python bridge/gateway.py

Important: the reference gateway's local command interlock uses FORTERA_ENABLE_HARDWARE. This variable is enabled here only for a supervised software SITL test. The adapter additionally enforces FORTERA_SITL_MODE=1, a loopback ws:// gateway URL and local MAVSDK endpoints. Do not copy this environment or these flags into a physical aircraft deployment. Unset them after SITL. A physical deployment requires a separate reviewed configuration and onboard safety system.

## 7. Confirm telemetry and operator arming
Wait for the gateway to report ready and for fresh position, home, health, battery and armed telemetry. If telemetry is missing or stale, stop here.

The bridge never arms or disarms. In QGroundControl, verify the target is the virtual SITL vehicle and the simulation is healthy. Only then, for the SITL test, arm locally using QGC. Do not add model-driven arm/disarm.

## 8. Generate a proposal first (no dispatch)
Terminal D:
    source .venv/bin/activate
    python -m companion.fortera_bridge "Take off to 10 metres" --vehicle F01 --language en

Inspect the JSON output. Default operation is proposal-only.

For a goto request, first configure a waypoint in the gateway environment. Example coordinates are illustrative SITL data only:
    export FORTERA_WAYPOINTS='[{"id":"sitl_gate","name":"SITL test point","lat":47.3978,"lon":8.5456,"relative_alt_m":10}]'

The waypoint must be inside the gateway's 100 m home envelope and pass all readiness/battery checks. The model can select only an ID already configured; it cannot create coordinates.

## 9. Explicit SITL-only dispatch
Only after the previous checks, run:
    export FORTERA_BRIDGE_TOKEN='<same private local token used by the gateway>'
    python -m companion.fortera_bridge "Take off to 10 metres" \
      --vehicle F01 --language en \
      --dispatch-sitl --confirm-sitl

The adapter connects to ws://127.0.0.1:8765, authenticates, verifies the gateway reports MAVSDK, checks configured waypoint IDs and sends a Bridge v2 command. The gateway repeats its own validation. The adapter maintains the required heartbeat while waiting for acknowledgement.

A gateway acknowledgement is not proof of takeoff, arrival, landing or mission completion. Check QGC and fresh telemetry. If the acknowledgement times out, do not retry automatically; inspect actual vehicle state first.

## 10. Acceptance test checklist
Record output and QGC evidence for each item:
1. PX4 SITL starts and QGC shows one virtual vehicle.
2. MAVSDK connects to F01 and fresh telemetry is received.
3. Fortera returns valid JSON using local inference.
4. Takeoff proposal maps value to altitude_m.
5. No dispatch occurs without both explicit CLI flags.
6. Unknown waypoint, unsupported action and invalid altitude are rejected.
7. Gateway repeats preflight and QGC confirms actual state after acknowledgement.
8. Hold is tested and observed in QGC.
9. Heartbeat loss is tested only in SITL; confirm actual PX4 failsafe response.
10. Stop all processes and unset SITL variables.

Do not mark integration as passed until these checks have actually run. Unit tests do not prove end-to-end SITL success.

## Official links
- PX4 simulation: https://docs.px4.io/main/en/simulation/
- PX4 Gazebo: https://docs.px4.io/main/en/sim_gazebo_gz/
- PX4 Ubuntu setup: https://docs.px4.io/main/en/dev_setup/dev_env_linux_ubuntu
- PX4 prebuilt SITL packages: https://docs.px4.io/main/en/simulation/px4_sitl_prebuilt_packages
- MAVSDK Python quick start: https://mavsdk.mavlink.io/main/en/python/quickstart/
- QGroundControl: https://qgroundcontrol.com/
