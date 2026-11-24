# Fortera bridge protocol v2

Status: reference integration, bench-tested. **Not a flight-certified product.** Browser flight/safety code is not transferred onto aircraft. `/demo` and `/swarm` remain independent simulations. `/bridge` pairs with an external companion process; public voice never sends hardware commands. No remote arming, disarming, motor values or raw MAVLink injection exists in this protocol.

## 1. Architecture

```text
Fortera browser → FleetAdapter → authenticated WSS → companion gateway
                                                   ├─ BenchBackend (default)
                                                   ├─ MAVSDK → MAVLink → PX4 / ArduPilot
                                                   └─ ROS 2 → Nav2 action server → ground robot
                                    ← telemetry / accepted / backend acknowledgement
Local pilot / RC override → flight controller (independent of browser and AI)
```

`src/hardware/fleet-adapter.ts` defines async FleetAdapter and WebSocketFleetAdapter. Unlike the legacy synchronous DroneAdapter, its promises express backend acknowledgement, not flight completion. The adapter does not import or modify either simulator store. One controller lease per bridge, 1–20 registered vehicles; call-sign mappings are gateway configuration, never guessed from a model response. Model proposals must be translated and independently safety-validated by an integration supervisor before dispatch; this supervisor is not yet deployed for physical missions.

The Python process in `bridge/` runs on Linux on a companion computer/workstation, **not inside the hosted web server**. Use MAVSDK for MAVLink rather than reimplementing framing or control loops. MAVSDK server ports are separated per aircraft. ROS 2 uses system packages and Nav2; a robot without Nav2 requires a new backend with the same contract.

## 2. Access and lifecycle

* Configure a private random `FORTERA_BRIDGE_TOKEN` (at least 24 characters) on the companion. Never put it in app source, a URL, logs or persistent browser storage. Pair manually; clear the browser field after connection. Provision over a trusted local channel; rotate after use.
* Require an exact Origin allowlist. There is no wildcard default. Origin is defence-in-depth, not identity; the secret is also mandatory.
* Remote traffic requires TLS (`wss://`) with a trusted certificate. Plain `ws://` is loopback-only. An HTTPS deployed browser may also require local-network permission or a trusted WSS endpoint; certificate bypasses are not supported.
* The first message must be `hello` within 5 seconds. Only one authenticated controller can hold the lease. Failed pairing closes the connection with code 1008.
* Send `ping` every second. After 5 seconds without a ping, close the connection, cancel pending gateway tasks and request backend hold. This is a reference software watchdog, **not a validated flight-controller failsafe**. Telemetry is sent at 5 Hz; the browser rejects new requests when telemetry is older than 2 seconds and disconnects after 5 seconds of silence.
* No automatic reconnect, retransmission or mission resumption on a hardware link. Re-pair and inspect current telemetry before any deliberate retry.
* Limits: 8 KiB inbound messages, 1,000 unique command IDs per session, at most three non-hold requests per second; one pending request per vehicle. Hold bypasses request throttling and preempts a pending request. Preemption cannot undo a command already accepted by the autopilot.

## 3. JSON messages

All messages carry `v: "fortera.bridge/2"`. Unknown versions and unsupported actions fail closed. No raw controller packets.

```json
{"v":"fortera.bridge/2","type":"hello","token":"<private token entered locally>"}
{"v":"fortera.bridge/2","type":"welcome","session":"opaque-session-id","mode":"bench","waypoints":[],"landing_radius_m":12}
{"v":"fortera.bridge/2","type":"ping"}
{"v":"fortera.bridge/2","type":"pong"}
```

A command contains one `action` or an `actions` plan (1–32 items) for one configured vehicle. Never send both. The entire plan is preflighted by client and gateway before any action is sent to the backend. Execution is sequential and each backend action rechecks current telemetry; a later runtime failure cannot roll back an already acknowledged command:

```json
{"v":"fortera.bridge/2","type":"command","id":"unique-uuid","vehicle":"F01","deadline_ms":1795262000000,"action":{"type":"takeoff","altitude_m":10}}
{"v":"fortera.bridge/2","type":"ack","id":"unique-uuid","status":"accepted"}
{"v":"fortera.bridge/2","type":"ack","id":"unique-uuid","status":"completed","reason":"Backend acknowledged request; arrival is not asserted"}
```

`deadline_ms` is Unix UTC milliseconds, between receipt time and receipt + 5,000 ms. Synchronise clocks on the companion and operator workstation. The example timestamp is illustrative; generate it at dispatch time. IDs are 1–64 characters. Identical IDs with identical envelopes return the cached status **without re-executing**; changing an envelope while reusing its ID is rejected. The cache is per connection, not a durable distributed exactly-once guarantee; never replay across sessions.

Statuses:

| Status | Meaning |
| --- | --- |
| accepted | Gateway validated and queued the request, not autopilot success |
| completed | Backend action API acknowledged the request; not arrival, landing or mission completion |
| rejected | Validation/access/capability/envelope failed before dispatch |
| failed | Backend error, preemption or timeout; outcome may be unknown |

A 15-second browser timeout means **unknown**, not a safe automatic retry. Actual position, armed state and mode must be read from subsequent telemetry. Nav2 `completed` here means goal acceptance, not navigation success; a future mission-result message must explicitly report the action result and final pose.

```json
{"v":"fortera.bridge/2","type":"telemetry","seq":42,"vehicles":[{"id":"F01","backend":"bench","lat":25.1972,"lon":55.2744,"relative_alt_m":10,"battery_pct":100,"armed":true,"ready":true,"mode":"HOLD","capabilities":["hold","takeoff","goto","return_home","land"],"home_lat":25.1972,"home_lon":55.2744}],"waypoints":[],"landing_radius_m":12}
```

Sequence numbers are monotonic per session; older frames are ignored. Browser receipt time measures freshness. ROS 2 additionally reports `local_x_m`, `local_y_m`; its lat/lon are zero and must not be interpreted as geographic location. Do not display a simulated position as physical telemetry.

## 4. Actions, coordinates and translation

| Action | Fields | Backend translation |
| --- | --- | --- |
| hold | none | MAVSDK action.hold; Nav2 cancel goal + zero Twist; bench holds |
| takeoff | altitude_m: 3–30 | MAVSDK set_takeoff_altitude + takeoff; no automatic arm |
| goto | waypoint_id: operator-configured id only | Gateway/backend resolves trusted coordinates; MAVSDK goto_location with AMSL = home AMSL + waypoint relative altitude and yaw 0; integration envelope 100 m from home |
| return_home | none | MAVSDK return_to_launch (controller owns its configured RTL altitude) |
| land | none | Home-radius check before MAVSDK land; default 12 m, otherwise reject with use return_home |
| navigate | x_m/y_m ±100, yaw_rad ±π | ROS 2 Nav2 NavigateToPose in `map` frame, metres / radians |

All numeric values must be finite. Unexpected fields are rejected. MAVSDK moving actions require locally armed aircraft, fresh global/home position; takeoff/goto require ≥30% reported battery. ROS 2 navigate requires fresh odometry and battery ≥30%, and a ready Nav2 server. **These narrow reference checks are not terrain, obstacle, people, NFZ, separation or safe-landing validation.** Those validations must be implemented onboard before field operation. A geographic map is not surveyed navigation data.

Never convert the procedural simulator's x/y/z into physical GPS targets. ROS 2 ENU/local map and PX4 NED/AMSL are different frames. MAVSDK owns the PX4 conversion; define and verify altitude datum, home origin, yaw convention and localisation before accepting targets. ROS 2 namespace/topic remapping must be set per robot. `cmd_vel` wiring differs between ROS distributions (Twist vs TwistStamped): this reference uses Twist and must be adapted/tested if the platform expects another message type.

## 4a. Version compatibility and operator-owned waypoints

Version 2 deliberately rejects version 1. Update browser client and gateway together, disconnect and re-pair; there is no downgrade or automatic replay. Version 1 arbitrary-coordinate goto is removed. A v2 goto payload is `{"type":"goto","waypoint_id":"gate"}`. Unknown IDs and coordinate fields fail closed.

The operator configures `FORTERA_WAYPOINTS` on the companion, as a JSON array:

```bash
export FORTERA_WAYPOINTS='[{"id":"gate","name":"Approved gate observation point","lat":25.1973,"lon":55.2744,"relative_alt_m":10}]'
export FORTERA_LANDING_RADIUS_M=12
```

This coordinate is illustrative bench data, not a validated physical flight site. Default waypoints are empty: the model never creates them. IDs must be unique, coordinates finite and relative altitude within 3–30 m. The landing radius must be finite and positive. Both welcome and every telemetry message include `waypoints` and `landing_radius_m`. Aircraft telemetry includes `home_lat` and `home_lon`; missing home data rejects goto/land. `FleetAdapter.get_scene()` exposes hardware telemetry with per-vehicle capabilities and the configured points, without importing simulator state. It is an integration API, not public Gemini-to-hardware wiring.

`dispatchPlan()` translates stop/hover to hold in all flight/battery/mission states, preflights the complete proposal, and sends one plan envelope. Unsupported simulator actions (approach, follow, orbit, inspect, look_at, scan_around, set_altitude, camera_pitch) reject the whole plan before sending. Takeoff outside 3–30 m rejects explicitly, never clamps. Takeoff/goto need at least 30% battery; hold/return_home/land have no charge threshold. Authentication, a known vehicle, the local hardware interlock and actual controller availability remain mandatory: a hold is never permission to bypass security, nor proof a disconnected controller received it. Hold bypasses stale-telemetry and mission-busy checks while an authenticated connection exists, and preempts a pending command.

Home-radius landing is a location check, not landing-site perception or certified landing safety. Real backends retain telemetry/local-arming requirements. The bench and MAVSDK backends revalidate waypoint, battery and home checks immediately before side effects. All checks remain reference integration rules; no hardware mode is enabled by this update.

## 5. Run the bench bridge (no hardware)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r bridge/requirements.txt
# Generate a private token in your local shell; never add it to source control.
export FORTERA_BRIDGE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FORTERA_ALLOWED_ORIGINS=http://localhost:8080
export FORTERA_BACKEND=bench
export FORTERA_BENCH_COUNT=5
python3 bridge/gateway.py
```

Open `/bridge`, enter `ws://127.0.0.1:8765` and the private local token. Verify five BENCH rows, bench takeoff, read altitude back, then request hold. Bench telemetry is synthetic, not a physical flight. It does not reuse the city's simulation state.

For a hosted browser, set the exact published origin in `FORTERA_ALLOWED_ORIGINS`, provide `FORTERA_TLS_CERT` / `FORTERA_TLS_KEY`, choose a reachable bind address via `FORTERA_BIND`, and pair over trusted WSS. Do not expose a token-less port or forward a flight-controller socket directly to the internet.

## 6. PX4 / MAVLink readiness

Install optional legacy `mavsdk<4` or `mavsdk-grpc` on the companion; use PX4 SITL first. Native MAVSDK v4 changes the System/plugin API and is not a drop-in dependency for this reference backend. Pin the Python package and matching MAVSDK server version tested with your autopilot for deployment; the server must support `MavlinkDirect`.

```bash
pip install 'mavsdk<4'
export FORTERA_BACKEND=mavsdk
export FORTERA_MAVSDK_ENDPOINTS='{"F01":"udpin://0.0.0.0:14540"}'
# Commands remain disabled unless explicitly enabled by the local operator.
# Enable only for supervised SITL/approved bench work after configuring controller safeguards.
export FORTERA_ENABLE_HARDWARE=1
python3 bridge/gateway.py
```

Do not enable hardware solely to try the public demo. Configure PX4/ArduPilot link-loss, low-battery, geofence, RC/pilot override and emergency procedures independently; confirm GPS/home/AMSL correctness. MAVLink serial/UDP endpoints and autopilot behaviour must be tested for the actual airframe. This code does not certify PX4 or ArduPilot support for every command/firmware. `/bridge` intentionally exposes only a hold request for non-bench backends; integration programs may use FleetAdapter for supervised tests after independent validation.

## 7. ROS 2 readiness

Source the robot's ROS 2 environment with `rclpy`, Nav2, `nav_msgs`, `sensor_msgs`, `geometry_msgs`. Launch localisation, odometry, battery publisher, collision monitor and Nav2 separately. Set `FORTERA_BACKEND=ros2`; hardware requests remain disabled until the local flag is enabled. This backend supports `R01`, `/odom`, `/battery_state`, `/cmd_vel` and `/navigate_to_pose`; apply namespace remappings to match the robot. It is for ground navigation, not aerial offboard control. No ROS 2 installation or physical robot is available in the hosted application.

## 8. Camera and deployment gaps

Video is intentionally separate from command JSON. Planned transport: WebRTC with authenticated signalling, short-lived access and explicit vehicle/track mapping; RTSP stays on the companion and can be converted by GStreamer. This implementation does **not** provide physical-camera streaming. No binary camera data or credentials in telemetry.

Before physical operations: validate command authentication and TLS, hardware interlocks, replay protection across reboots, durable audit logging, local safety supervisor, onboard link-loss response, separation for multiple aircraft, landing-site selection, perception calibration, action completion tracking and supervised SITL → hardware-in-loop → controlled field trials. Obtain all required approvals. Simulator tests and OSM maps are not evidence of aircraft safety.


## 9. Offline training-data preflight

See `docs/training-dataset.md`. Run `bun scripts/validate-dataset.ts scripts/fixtures/training-examples.jsonl` to obtain JSON decisions without starting a gateway, connecting hardware, calling a model or executing flight. This validates labels/proposals, not model quality or aircraft safety.

## 10. Receive-only obstacle sensing and conservative restrictions

The companion subscribes through MAVSDK's `system.mavlink_direct.message("DISTANCE_SENSOR")` and `message("OBSTACLE_DISTANCE")`. It reads `fields_json` only: no raw-message transmission, sensor configuration, autopilot parameter changes or new avoidance manoeuvres. Plugin absence/server incompatibility means unavailable sensing, never verified avoidance. MAVSDK/airframe operation is not validated in this sandbox; subscriptions have been exercised with mocked message streams only. Isolate each configured endpoint to its intended aircraft and verify sender IDs, mounting, units and frame in supervised SITL/bench work before relying on data.

Per-vehicle telemetry and `FleetAdapter.get_scene().vehicles` may include:

```json
{"obstacle_sensing":{"healthy":true,"updated_ms_ago":120,"nearest_m":14,"sectors":[{"sector":"forward","distance_m":14,"updated_ms_ago":120}]},"capabilities":["hold","takeoff","goto","obstacle_sensing"]}
```

Only measured sectors are listed; **healthy is freshness of received coverage, not full-surround sensor coverage or aircraft safety certification**. Distances are metres converted from MAVLink centimetres. Individual source/sensor/bin samples use the companion monotonic clock; all observed coverage must be valid and strictly younger than 1,000 ms. Fresh data in one sector cannot mask stale data elsewhere. Unknown/invalid measurements are never clear. The dynamic `obstacle_sensing` capability disappears when sensing becomes unhealthy/stale, but the sensing record is retained so removing the capability cannot bypass preflight. Browser scene reads add time since receipt to sensor ages.

DISTANCE_SENSOR yaw orientations 0–7 map to body-relative 45° sectors; pitch 90/270 maps to up/down. Custom/unsupported orientation or invalid signal invalidates coverage instead of inventing a direction. OBSTACLE_DISTANCE accepts BODY_FRD frame 12 only; global/north-aligned data is not silently reinterpreted without a validated attitude conversion. `increment_f` takes precedence (including negative values), otherwise `increment`; angle_offset is applied. A value 65535 is unknown/unused, zero is contact, and max_distance+1 means clear **only to the measurement maximum**. These conventions require physical mounting/firmware verification.

`FORTERA_MIN_CLEARANCE_M` is a finite positive companion setting, default **10 m**; welcome/telemetry expose `min_clearance_m` so client and gateway agree. If sensing exists, takeoff/goto reject with `obstacle_too_close` below that minimum, or `obstacle_data_stale` for unhealthy/expired readings. Hold/stop/hover bypass these sensor checks, not authentication or the local hardware interlock. No historical sensing yet means unavailable sensing, not an assurance that takeoff/goto is obstacle-safe.

The gateway's 10 Hz watchdog runs independently of a paired browser. In flight (armed and altitude >0.6 m), expired/invalid observed sensing preempts pending requests, requests the existing backend hold and publishes `safety_reason: "obstacle_data_stale"`. It does not replay or resume the mission. Actual hold acknowledgement is not proof of stopping; autopilot-owned failsafes are essential. **Disabled physical commands remain disabled**, including this software watchdog; the bridge reports the reason but does not silently enable equipment. Fresh recovery clears the sensor warning, never auto-replays a cancelled request.

Hardware orbit, follow, approach and inspect remain rejected: **"Unsupported action: requires verified obstacle avoidance; not enabled"**. This update implements reception and restrictions, not a physical obstacle-avoidance planner.

### Autopilot configuration belongs to the qualified local operator

* **PX4:** install/calibrate and verify suitable range/proximity sensors and their MAVLink forwarding; configure Collision Prevention with `CP_DIST` and related firmware-specific collision-prevention settings. Verify supported flight modes, sensor orientation/coverage, braking dynamics, and the controller's reaction to missing coverage and link loss. `CP_DIST` is in metres and must reflect airframe/sensor performance and an independently verified clearance policy; do not assume every goto/mission mode is protected by Collision Prevention.
* **ArduPilot:** configure the airframe/firmware-specific rangefinder and proximity sources, orientation and coverage (e.g. `PRX*_TYPE`), then the applicable avoidance behaviour and margins (`AVOID_ENABLE`, `AVOID_MARGIN`, and the relevant obstacle-avoidance planner settings). Names, units and mode support vary by firmware; use its official documentation and supervised tests, not copied settings from another aircraft.

**The bridge does not enable or configure PX4 Collision Prevention or ArduPilot proximity/avoidance, does not verify their parameters, and has not verified them in real flight.** Sensor telemetry, simulator clearance tests and backend acknowledgement do not constitute onboard protection. Keep local pilot override and independently configured link-loss/battery/geofence safeguards.