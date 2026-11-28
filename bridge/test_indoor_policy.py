import unittest
from policy import preflight, resolve_profile, TELLO_INDOOR_PROFILE

PERC=dict(source='bridge_cv',warning_only=True,frame_seq=10,frame_age_ms=80,video_fps=15,updated_ms_ago=60,frame_status='ok',brightness=120,sharpness=300,texture_points=150,flow=dict(median_px=0.5,divergence=0,consistency=1),obstacle_state='no_warning',warnings=[],confidence=0.9)
SDK=dict(model='tello',sdk_version=None,state_age_ms=50,height_cm=100,tof_cm=110,baro_m=1,pitch_deg=0,roll_deg=0,yaw_deg=0,vgx=0,vgy=0,vgz=0,temp_high_c=60,flight_time_s=30,wifi_snr=90,vps_hint='likely_ok')
EST=dict(kind='dead_reckoning_commanded',valid=True,x_m=0,y_m=0,yaw_deg=0,uncertainty_m=0.1,steps_since_reset=0)
T=dict(id='T01',backend='tello',lat=0,lon=0,relative_alt_m=1.0,battery_pct=80,armed=True,ready=True,mode='FLYING',capabilities=['hold','land','return_home','takeoff','navigate'],local_x_m=0,local_y_m=0,perception=PERC,sdk_state=SDK,estimate=EST)
IN=resolve_profile('indoor_demo')
def v(**kw): return {**T,**kw}
def nav(x=0.3,y=0,yaw=0): return {'type':'navigate','x_m':x,'y_m':y,'yaw_rad':yaw}

class IndoorPolicyTests(unittest.TestCase):
    def test_healthy_short_step_and_land_hold_always(self):
        self.assertEqual(preflight(v(),nav(),[],profile=IN)['x_m'],0.3)
        bad=v(perception={**PERC,'frame_status':'lost'},estimate={**EST,'valid':False,'invalid_reason':'stop'})
        for kind in ('land','hold'): self.assertEqual(preflight(bad,{'type':kind},[],profile=IN)['type'],kind)
    def test_missing_perception_blocks_takeoff_and_moves(self):
        no=v(relative_alt_m=0);no.pop('perception')
        with self.assertRaisesRegex(ValueError,'local camera checks'):preflight(no,{'type':'takeoff','altitude_m':1},[],profile=IN)
    def test_stale_black_lost_unknown_warning_block(self):
        for p,msg in [({'frame_status':'stale'},'stale'),({'frame_status':'black'},'black'),({'frame_age_ms':900},'older than'),({'confidence':None},'unknown'),({'confidence':0.3},'below'),({'obstacle_state':'unknown'},'cannot tell'),({'obstacle_state':'warning','warnings':[{'kind':'looming','region':'center','confidence':0.8}]},'looming')]:
            with self.subTest(p=p),self.assertRaisesRegex(ValueError,msg):preflight(v(perception={**PERC,**p}),nav(),[],profile=IN)
    def test_takeoff_only_to_tello_height_and_battery(self):
        with self.assertRaisesRegex(ValueError,'only to 1.2'):preflight(v(relative_alt_m=0),{'type':'takeoff','altitude_m':2},[],profile=IN)
        with self.assertRaisesRegex(ValueError,'at least 50%'):preflight(v(relative_alt_m=0,battery_pct=45),{'type':'takeoff','altitude_m':1},[],profile=IN)
        with self.assertRaisesRegex(ValueError,'at least 40%'):preflight(v(battery_pct=35),nav(),[],profile=IN)
    def test_step_turn_axis_envelope(self):
        with self.assertRaisesRegex(ValueError,'0.2–0.5'):preflight(v(),nav(0.8),[],profile=IN)
        with self.assertRaisesRegex(ValueError,'one axis'):preflight(v(),nav(0.3,0.3),[],profile=IN)
        with self.assertRaisesRegex(ValueError,'45'):preflight(v(),nav(0,0,1.2),[],profile=IN)
        with self.assertRaisesRegex(ValueError,'2 m area'):preflight(v(estimate={**EST,'x_m':1.5,'uncertainty_m':0.3}),nav(0.4),[],profile=IN)
    def test_invalid_estimate_blocks_moves_and_return(self):
        bad=v(estimate={**EST,'valid':False,'invalid_reason':'interrupted'})
        for a in (nav(),{'type':'return_home'}):
            with self.subTest(a=a),self.assertRaisesRegex(ValueError,'invalid'):preflight(bad,a,[],profile=IN)
    def test_stale_state_temperature_and_flight_time(self):
        for s,msg in [({'state_age_ms':600},'older than'),({'temp_high_c':85},'temperature'),({'flight_time_s':300},'Flight time')]:
            with self.subTest(s=s),self.assertRaisesRegex(ValueError,msg):preflight(v(sdk_state={**SDK,**s}),nav(),[],profile=IN)
    def test_settling_flow_blocks_next_step(self):
        with self.assertRaisesRegex(ValueError,'settle'):preflight(v(perception={**PERC,'flow':dict(median_px=9,divergence=0,consistency=1)}),nav(),[],profile=IN)
    def test_profile_overrides_only_tighten(self):
        p=resolve_profile('indoor_demo',{'step_max_m':2,'battery_move_pct':20,'envelope_m':1.5})
        self.assertEqual(p['limits']['step_max_m'],0.5);self.assertEqual(p['limits']['battery_move_pct'],40);self.assertEqual(p['limits']['envelope_m'],1.5)
        with self.assertRaises(ValueError):resolve_profile('indoor_demo',{'nope':1})
    def test_standard_profile_without_perception_keeps_old_behaviour(self):
        legacy=dict(T);[legacy.pop(k) for k in ('perception','sdk_state','estimate')]
        self.assertEqual(preflight(legacy,nav(5),[],profile=resolve_profile('standard'))['x_m'],5)
        self.assertEqual(preflight(legacy,nav(5),[])['x_m'],5)
    def test_pad_step_requires_edu_detected_pad(self):
        with self.assertRaisesRegex(ValueError,'Tello EDU'):preflight(v(),{'type':'pad_step','mid':1,'x_cm':0,'y_cm':0,'z_cm':80},[],profile=IN)
        edu=v(capabilities=T['capabilities']+['mission_pad'],sdk_state={**SDK,'model':'tello_edu'},mission_pad=dict(enabled=True,direction='down',mid=1,x_cm=0,y_cm=0,z_cm=80,age_ms=50,corridor=[]))
        self.assertEqual(preflight(edu,{'type':'pad_step','mid':1,'x_cm':0,'y_cm':0,'z_cm':80},[],profile=IN)['mid'],1)
        with self.assertRaisesRegex(ValueError,'not detected'):preflight(edu,{'type':'pad_step','mid':2,'x_cm':0,'y_cm':0,'z_cm':80},[],profile=IN)
    def test_defaults_match_browser(self):
        self.assertEqual(TELLO_INDOOR_PROFILE['limits']['step_max_m'],0.5);self.assertEqual(TELLO_INDOOR_PROFILE['limits']['frame_max_age_ms'],500)

if __name__=='__main__': unittest.main()