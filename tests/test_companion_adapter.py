import os
import unittest
from unittest.mock import patch
from fortera_runtime.client import ForteraError
from companion.fortera_bridge import proposal_to_bridge_actions, make_command, assert_local_sitl_environment

def proposal(actions):
    return {"mode":"hardware","calls":[{"name":"drone_control","arguments":{"actions":actions}}]}

class AdapterTests(unittest.TestCase):
    def test_takeoff_mapping(self):
        self.assertEqual(proposal_to_bridge_actions(proposal([{"type":"takeoff","value":12}])),
                         [{"type":"takeoff","altitude_m":12.0}])
    def test_goto_is_waypoint_only(self):
        self.assertEqual(proposal_to_bridge_actions(proposal([{"type":"goto","waypoint_id":"gate"}])),
                         [{"type":"goto","waypoint_id":"gate"}])
    def test_hover_maps_to_hold(self):
        self.assertEqual(proposal_to_bridge_actions(proposal([{"type":"hover"}])), [{"type":"hold"}])
    def test_hold_maps_to_hold(self):
        self.assertEqual(proposal_to_bridge_actions(proposal([{"type":"hold"}])), [{"type":"hold"}])
    def test_refusal_rejected(self):
        x={"mode":"hardware","calls":[],"safety_result":{"reason":"blocked"}}
        with self.assertRaises(ForteraError): proposal_to_bridge_actions(x)
    def test_unknown_action_rejected(self):
        with self.assertRaises(ForteraError): proposal_to_bridge_actions(proposal([{"type":"arm"}]))
    def test_command_deadline_and_protocol(self):
        x=make_command([{"type":"hold"}],"F01",command_id="test",now_ms=1000)
        self.assertEqual(x["v"],"fortera.bridge/2")
        self.assertEqual(x["deadline_ms"],5000)
    def test_remote_dispatch_rejected(self):
        with patch.dict(os.environ, {"FORTERA_SITL_MODE":"1","FORTERA_BACKEND":"mavsdk",
            "FORTERA_MAVSDK_ENDPOINTS":'{"F01":"udpin://127.0.0.1:14540"}',
            "FORTERA_ENABLE_HARDWARE":"1"}, clear=False):
            with self.assertRaises(RuntimeError):
                assert_local_sitl_environment("wss://example.com:8765")

if __name__ == "__main__": unittest.main()
