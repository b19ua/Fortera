import unittest
from policy import preflight, preflight_plan

T=dict(id='T01',backend='tello',lat=0,lon=0,relative_alt_m=1.2,battery_pct=80,armed=True,ready=True,mode='FLYING',capabilities=['hold','land','return_home','takeoff','navigate'],local_x_m=0,local_y_m=0)
def v(**kw): return {**T,**kw}

class TelloPolicyTests(unittest.TestCase):
    def test_takeoff_range(self):
        self.assertEqual(preflight(v(relative_alt_m=0),{'type':'takeoff','altitude_m':1},[]),{'type':'takeoff','altitude_m':1})
        with self.assertRaisesRegex(ValueError,'between 1 and 10'):preflight(v(relative_alt_m=0),{'type':'takeoff','altitude_m':12},[])
    def test_no_gps_goto_even_if_advertised(self):
        with self.assertRaisesRegex(ValueError,'no GPS'):preflight(v(capabilities=T['capabilities']+['goto']),{'type':'goto','waypoint_id':'gate'},[{'id':'gate','name':'G','lat':0,'lon':0,'relative_alt_m':10}])
    def test_step_and_envelope(self):
        self.assertEqual(preflight(v(),{'type':'navigate','x_m':5,'y_m':0,'yaw_rad':0},[])['x_m'],5)
        with self.assertRaisesRegex(ValueError,'at most 5 m'):preflight(v(),{'type':'navigate','x_m':6,'y_m':0,'yaw_rad':0},[])
        with self.assertRaisesRegex(ValueError,'15 m'):preflight(v(local_x_m=12),{'type':'navigate','x_m':4,'y_m':0,'yaw_rad':0},[])
        with self.assertRaisesRegex(ValueError,'airborne'):preflight(v(relative_alt_m=0),{'type':'navigate','x_m':1,'y_m':0,'yaw_rad':0},[])
    def test_plan_tracks_takeoff_and_worst_case_radius(self):
        steps=[{'type':'takeoff','altitude_m':2}]+[{'type':'navigate','x_m':5,'y_m':0,'yaw_rad':0}]*3
        self.assertEqual(len(preflight_plan(v(relative_alt_m=0),steps,[])),4)
        with self.assertRaisesRegex(ValueError,'15 m'):preflight_plan(v(),[{'type':'navigate','x_m':5,'y_m':0,'yaw_rad':0}]*4,[])
    def test_lands_in_place(self):
        self.assertEqual(preflight(v(),{'type':'land'},[]),{'type':'land'})

if __name__=='__main__': unittest.main()