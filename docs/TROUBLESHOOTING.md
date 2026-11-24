# Troubleshooting

## Local model
**llama-server not found:** install/build llama.cpp for the current OS/CPU and check command -v llama-server.

**Model not found:** download Fortera-Q4_K_M.gguf from https://huggingface.co/Askit87/Fortera and set FORTERA_MODEL_PATH.

**Tokenizer load failure:** download the complete tokenizer set from the same HF release and set FORTERA_TOKENIZER_PATH. Do not mix model/tokenizer revisions.

**Local server unavailable:** verify llama-server is listening on 127.0.0.1:8081. Do not expose the inference port publicly.

**Invalid JSON or unsupported action:** treat it as rejected. Do not loosen validation to make a request pass. Try a simpler instruction and verify model/tokenizer version.

## Companion Bridge
**Pairing rejected:** check exact Origin allowlist, private token length (24+ chars), and whether another controller owns the lease.

**Gateway will not start:** install bridge/requirements.txt; check FORTERA_BACKEND is bench, mavsdk or ros2; check port 8765; non-loopback binds require TLS certificate and key.

**Command rejected:** check vehicle ID, action support, configured waypoint, telemetry freshness, battery, home and local interlock.

**Timeout or unknown outcome:** do not retry automatically. Inspect QGC/current telemetry first.

## PX4 SITL and MAVSDK
**No vehicle discovered:** confirm SITL is running; default developer API is normally UDP 14540 and QGC normally uses UDP 14550. Verify the running PX4 output and the active instance's port mapping.

**MAVSDK backend fails to start:** check compatibility between the legacy System API in this reference backend, installed mavsdk/mavsdk-grpc package and matching server. Do not mix versions blindly.

**Vehicle not ready:** wait for fresh global position, home, health, battery and armed telemetry. Do not bypass checks. Arm only the virtual vehicle locally in QGroundControl after confirming SITL is healthy.

**Goto rejected:** waypoint ID must be in FORTERA_WAYPOINTS; coordinates must be finite and inside the gateway's 100 m home envelope; battery and home/GPS checks must pass.

**Adapter blocks dispatch:** proposal-only is the default. For SITL only, follow PX4_SITL.md exactly. Dispatch requires local bridge URL, local MAVSDK endpoints and FORTERA_SITL_MODE=1. Never reuse these flags on a physical vehicle.

## Unexpected behaviour
1. Stop sending new commands.
2. Use independent local pilot/ground-station controls and autopilot failsafes.
3. Preserve logs and telemetry.
4. Disconnect the adapter and inspect actual vehicle state.
5. Never auto-replay a mission after reconnect.
