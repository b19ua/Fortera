import math, unittest
from tello_estimate import EstimateTracker

class EstimateTests(unittest.TestCase):
    def setUp(self): self.e=EstimateTracker();self.e.reset_at_takeoff(0)
    def test_invalid_until_takeoff(self):
        e=EstimateTracker();s=e.snapshot();self.assertFalse(s['valid']);self.assertEqual(s['invalid_reason'],'not_airborne')
    def test_body_frame_steps_and_growing_uncertainty(self):
        self.e.apply_step(0.5,0,math.pi/2);self.e.apply_step(0.5,0,0)
        s=self.e.snapshot();self.assertAlmostEqual(s['x_m'],0.5,2);self.assertAlmostEqual(s['y_m'],0.5,2);self.assertGreater(s['uncertainty_m'],0.25);self.assertEqual(s['steps_since_reset'],2)
    def test_stop_interrupt_and_reconnect_invalidate_and_stay_invalid(self):
        for reason in ('stop','interrupted','reconnect','sdk_error'):
            e=EstimateTracker();e.reset_at_takeoff(0);e.invalidate(reason);e.apply_step(1,0,0)
            s=e.snapshot();self.assertFalse(s['valid']);self.assertEqual(s['invalid_reason'],reason);self.assertEqual(s['x_m'],0)
    def test_telemetry_gap(self):
        self.e.observe_state({'yaw':0},1.5,True,10);self.assertEqual(self.e.snapshot()['invalid_reason'],'telemetry_gap')
    def test_unexplained_yaw_jump(self):
        self.e.observe_state({'yaw':30},0.1,True,10);self.assertEqual(self.e.snapshot()['invalid_reason'],'yaw_jump')
    def test_commanded_turn_is_expected(self):
        self.e.apply_step(0,0,math.radians(30));self.e.observe_state({'yaw':-30,'vgx':0,'vgy':0},0.1,True,10);self.assertTrue(self.e.snapshot()['valid'])
    def test_uncommanded_motion_after_grace(self):
        self.e.begin_command();self.e.end_command(10)
        self.e.observe_state({'yaw':0,'vgx':4},0.1,True,10.5);self.assertTrue(self.e.snapshot()['valid'])
        self.e.observe_state({'yaw':0,'vgx':4},0.1,True,12);self.assertEqual(self.e.snapshot()['invalid_reason'],'unconfirmed_action')
    def test_landing(self):
        self.e.observe_state({'yaw':0},0.1,False,10);self.assertEqual(self.e.snapshot()['invalid_reason'],'not_airborne')

if __name__=='__main__': unittest.main()