"""Fortera-to-Companion-Bridge adapter.

Default behaviour is proposal-only. Dispatch is limited to an explicitly
confirmed local PX4 SITL endpoint; this module does not arm or disarm vehicles.
"""
import argparse
import asyncio
import json
import os
import secrets
import time
from urllib.parse import urlparse

from fortera_runtime.client import infer, ForteraError

PROTOCOL = "fortera.bridge/2"
ALLOWED = {"takeoff", "goto", "hold", "hover"}


def proposal_to_bridge_actions(proposal):
    if not isinstance(proposal, dict) or proposal.get("mode") != "hardware":
        raise ForteraError("Expected a hardware-schema proposal for Bridge v2")
    if proposal.get("safety_result") is not None:
        raise ForteraError("Fortera refused the request; no bridge command created")
    calls = proposal.get("calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ForteraError("Expected exactly one drone_control call")
    call = calls[0]
    if not isinstance(call, dict) or call.get("name") != "drone_control":
        raise ForteraError("Unsupported tool call")
    args = call.get("arguments")
    if not isinstance(args, dict) or set(args) != {"actions"}:
        raise ForteraError("Invalid drone_control arguments")
    raw = args["actions"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise ForteraError("Expected 1–8 actions")
    result = []
    for item in raw:
        if not isinstance(item, dict) or item.get("type") not in ALLOWED:
            raise ForteraError("Action is not supported by the Fortera Bridge adapter")
        kind = item["type"]
        if kind == "takeoff":
            if set(item) != {"type", "value"}:
                raise ForteraError("Invalid takeoff proposal")
            value = item["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 3 <= value <= 30:
                raise ForteraError("Takeoff proposal must be 3–30 metres")
            result.append({"type": "takeoff", "altitude_m": float(value)})
        elif kind == "goto":
            if set(item) != {"type", "waypoint_id"} or not isinstance(item["waypoint_id"], str):
                raise ForteraError("Invalid goto proposal")
            result.append({"type": "goto", "waypoint_id": item["waypoint_id"]})
        else:
            if set(item) != {"type"}:
                raise ForteraError("Unexpected hold/hover parameters")
            result.append({"type": "hold"})
    return result


def make_command(actions, vehicle, command_id=None, now_ms=None):
    if not isinstance(vehicle, str) or not vehicle or len(vehicle) > 64:
        raise ValueError("A configured vehicle ID is required")
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return {
        "v": PROTOCOL,
        "type": "command",
        "id": command_id or secrets.token_hex(16),
        "vehicle": vehicle,
        "deadline_ms": now + 4000,
        "actions": actions,
    }


def assert_local_sitl_environment(url):
    if os.getenv("FORTERA_SITL_MODE") != "1":
        raise RuntimeError("Dispatch blocked: set FORTERA_SITL_MODE=1 only for a local PX4 SITL test")
    parsed = urlparse(url)
    if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("SITL dispatch requires ws:// on loopback; remote/WSS targets are not accepted")
    if os.getenv("FORTERA_BACKEND") != "mavsdk":
        raise RuntimeError("SITL dispatch requires FORTERA_BACKEND=mavsdk on the companion gateway")
    endpoint_map = json.loads(os.getenv("FORTERA_MAVSDK_ENDPOINTS", '{"F01":"udpin://127.0.0.1:14540"}'))
    if not endpoint_map or any(urlparse(v).hostname not in {"127.0.0.1", "localhost", "::1"} for v in endpoint_map.values()):
        raise RuntimeError("All MAVSDK endpoints must be loopback/local for SITL")
    if os.getenv("FORTERA_ENABLE_HARDWARE") != "1":
        raise RuntimeError("Gateway local interlock is off; enable only for this supervised SITL session")


async def dispatch_sitl(command, url, token):
    assert_local_sitl_environment(url)
    if not token or len(token) < 24:
        raise RuntimeError("FORTERA_BRIDGE_TOKEN must be set locally (24+ characters)")
    import websockets
    async with websockets.connect(url, max_size=8192, open_timeout=5) as ws:
        await ws.send(json.dumps({"v": PROTOCOL, "type": "hello", "token": token}))
        welcome = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if welcome.get("v") != PROTOCOL or welcome.get("type") != "welcome" or welcome.get("mode") != "mavsdk":
            raise RuntimeError("Bridge handshake failed or backend is not MAVSDK")
        configured = {p.get("id") for p in welcome.get("waypoints", [])}
        for action in command["actions"]:
            if action["type"] == "goto" and action["waypoint_id"] not in configured:
                raise RuntimeError("Waypoint is not configured in the gateway")
        await ws.send(json.dumps(command))
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), max(0.1, deadline-time.monotonic()))
            msg = json.loads(raw)
            if msg.get("type") == "ack" and msg.get("id") == command["id"]:
                if msg.get("status") not in {"accepted", "completed"}:
                    raise RuntimeError("Gateway rejected command: " + str(msg.get("reason", "unknown")))
                if msg.get("status") == "completed":
                    return msg
        raise TimeoutError("No final acknowledgement; inspect telemetry before any retry")


def main():
    p = argparse.ArgumentParser(description="Fortera proposal adapter for Companion Bridge v2")
    p.add_argument("instruction")
    p.add_argument("--vehicle", default="F01")
    p.add_argument("--language", choices=["en", "ru", "ar", "de"], default="en")
    p.add_argument("--waypoints", default="")
    p.add_argument("--dispatch-sitl", action="store_true", help="requires explicit --confirm-sitl")
    p.add_argument("--confirm-sitl", action="store_true")
    p.add_argument("--bridge-url", default=os.getenv("FORTERA_BRIDGE_URL", "ws://127.0.0.1:8765"))
    args = p.parse_args()
    if args.dispatch_sitl and not args.confirm_sitl:
        p.error("--dispatch-sitl requires --confirm-sitl")
    waypoints = set(filter(None, args.waypoints.split(","))) or None
    proposal = infer(args.instruction, "hardware", args.language, waypoints=waypoints)
    actions = proposal_to_bridge_actions(proposal)
    command = make_command(actions, args.vehicle)
    print(json.dumps({"status": "proposal_ready", "dispatch_enabled": False,
                      "requires_operator_confirmation": True, "bridge_command": command},
                     ensure_ascii=False, indent=2))
    if args.dispatch_sitl:
        token = os.getenv("FORTERA_BRIDGE_TOKEN", "")
        result = asyncio.run(dispatch_sitl(command, args.bridge_url, token))
        print(json.dumps({"status": "sitl_gateway_acknowledged",
                          "note": "Acknowledgement is not proof of arrival or mission completion.",
                          "ack": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
