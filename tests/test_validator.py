import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fortera_runtime.client import validate,normalize,ForteraError
class Tests(unittest.TestCase):
 def test_takeoff(self):
  x={"mode":"simulator","calls":[{"name":"drone_control","arguments":{"actions":[{"type":"takeoff","value":15}]}}]}
  self.assertEqual(validate(x,"simulator"),x)
 def test_refusal(self):
  x={"mode":"hardware","calls":[],"safety_result":{"reason":"obstacle_data_stale"},"response":{"type":"refusal","reason":"obstacle_data_stale"}}
  self.assertEqual(validate(x,"hardware"),x)
 def test_refusal_cannot_move(self):
  x={"mode":"hardware","calls":[{"name":"drone_control","arguments":{"actions":[{"type":"takeoff","value":10}]}}],"safety_result":{"reason":"blocked"},"response":{"type":"refusal","reason":"blocked"}}
  with self.assertRaises(ForteraError): validate(x,"hardware")
 def test_altitude(self):
  x={"mode":"hardware","calls":[{"name":"drone_control","arguments":{"actions":[{"type":"takeoff","value":50}]}}]}
  with self.assertRaises(ForteraError): validate(x,"hardware")
 def test_waypoint(self):
  x={"mode":"hardware","calls":[{"name":"drone_control","arguments":{"actions":[{"type":"goto","waypoint_id":"home"}]}}]}
  with self.assertRaises(ForteraError): validate(x,"hardware",{"gate"})
 def test_waypoint_requires_allowlist(self):
  x={"mode":"hardware","calls":[{"name":"drone_control","arguments":{"actions":[{"type":"goto","waypoint_id":"home"}]}}]}
  with self.assertRaises(ForteraError): validate(x,"hardware")
 def test_simulator_alias(self):
  x={"mode":"simulator","calls":[{"name":"drone_control","arguments":{"actions":[{"type":"hold"}]}}]}
  self.assertEqual(normalize(x)["calls"][0]["arguments"]["actions"][0]["type"],"hover")
 def test_unknown_action(self):
  x={"mode":"hardware","calls":[{"name":"drone_control","arguments":{"actions":[{"type":"arm"}]}}]}
  with self.assertRaises(ForteraError): validate(x,"hardware")
if __name__=="__main__":unittest.main()
