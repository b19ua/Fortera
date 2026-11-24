# Fortera architecture and trust boundaries

## Data flow
    Operator text
       -> Fortera GGUF + llama.cpp on companion
       -> structured proposal JSON
       -> fortera_runtime validation
       -> companion.fortera_bridge translation
       -> explicit operator opt-in for SITL
       -> authenticated Bridge v2 WebSocket
       -> companion gateway + independent policy preflight
       -> MAVSDK backend
       -> PX4 SITL + Gazebo
       -> QGroundControl telemetry readback

## Responsibilities
- **Fortera model:** interprets language and proposes constrained actions. Model output is untrusted.
- **Runtime validator:** checks JSON schema and action fields.
- **Bridge adapter:** maps the model's action schema to Bridge v2. takeoff.value becomes takeoff.altitude_m; hover becomes hold. Unsupported actions are rejected.
- **Gateway:** authenticates the session, validates command envelope, vehicle, deadlines, waypoint allowlist and current policy checks.
- **MAVSDK:** handles MAVLink API communication; no raw MAVLink injection from the model.
- **PX4:** owns stabilization and autopilot failsafes.
- **Local operator:** owns arming, disarming, override and supervision.

## Current integration scope
Adapter supports takeoff, goto, hold and hover proposals only. It does not implement return-to-home, land, arm/disarm, camera control, obstacle avoidance, follow, orbit, multi-aircraft mission planning or physical camera transport.

## SITL-only dispatch gate
The adapter requires all of the following for dispatch:
- explicit CLI flags --dispatch-sitl and --confirm-sitl;
- FORTERA_SITL_MODE=1;
- loopback ws:// Bridge URL;
- FORTERA_BACKEND=mavsdk;
- every MAVSDK endpoint is local/loopback;
- FORTERA_ENABLE_HARDWARE=1 for the existing gateway interlock;
- private bridge token.

These guards reduce accidental remote dispatch; they are not a secure physical-flight authorization system. The gateway repeats policy checks.

## Known gaps
- No end-to-end PX4 SITL execution recorded yet.
- No hardware, HITL or field test.
- No production TLS deployment or browser-to-local bridge pairing validation in this repository.
- No complete obstacle avoidance, geofence/NFZ, terrain or people-separation planner.
- No physical camera stream.
- No Raspberry Pi performance benchmark.
- No durable cross-session exactly-once command journal.
- Backend acknowledgement does not prove vehicle arrival.
