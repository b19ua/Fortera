import unittest
import numpy as np
import perception as P
from perception import PerceptionMonitor

rng=np.random.default_rng(1)
TEX=(rng.random((240,320))*255).astype(np.uint8)
def zoom(img,f):
    import cv2
    h,w=img.shape;z=cv2.resize(img,None,fx=f,fy=f);y0=(z.shape[0]-h)//2;x0=(z.shape[1]-w)//2
    return z[y0:y0+h,x0:x0+w]

class PerceptionTests(unittest.TestCase):
    def test_no_clear_state_ever(self):
        m=PerceptionMonitor()
        for i in range(5): m.push_frame(np.roll(TEX,i,axis=1),i*0.05)
        s=m.snapshot(0.25)
        self.assertTrue(s['warning_only']);self.assertIn(s['obstacle_state'],('unknown','warning','no_warning'));self.assertEqual(s['frame_status'],'ok')
        self.assertEqual(s['obstacle_state'],'no_warning');self.assertGreaterEqual(s['confidence'],0.6)
    def test_lost_and_decoding_before_any_frame(self):
        m=PerceptionMonitor();m.start(0)
        self.assertEqual(m.snapshot(0.5)['frame_status'],'decoding');self.assertEqual(m.snapshot(3)['frame_status'],'lost')
        self.assertIsNone(m.snapshot(3)['confidence']);self.assertEqual(m.snapshot(3)['obstacle_state'],'unknown')
    def test_stale_then_lost_by_age(self):
        m=PerceptionMonitor(stale_ms=500,lost_ms=2000)
        for i in range(3): m.push_frame(np.roll(TEX,i,axis=1),i*0.05)
        self.assertEqual(m.snapshot(0.2)['frame_status'],'ok')
        s=m.snapshot(0.8);self.assertEqual(s['frame_status'],'stale');self.assertIsNone(s['confidence']);self.assertEqual(s['obstacle_state'],'unknown')
        self.assertEqual(m.snapshot(3)['frame_status'],'lost')
    def test_black_frame(self):
        m=PerceptionMonitor()
        for i in range(3): m.push_frame(np.zeros((240,320),np.uint8),i*0.05)
        s=m.snapshot(0.12);self.assertEqual(s['frame_status'],'black');self.assertIsNone(s['confidence'])
    def test_frozen_stream(self):
        m=PerceptionMonitor(frozen_frames=10)
        for i in range(12): m.push_frame(TEX,i*0.05)
        s=m.snapshot(0.56);self.assertEqual(s['frame_status'],'frozen');self.assertIn('frozen',[w['kind'] for w in s['warnings']])
    def test_featureless_view_is_unknown_not_clear(self):
        m=PerceptionMonitor()
        for i in range(4): m.push_frame(np.full((240,320),128,np.uint8)+rng.integers(0,2,(240,320)).astype(np.uint8),i*0.05)
        s=m.snapshot(0.16);self.assertEqual(s['obstacle_state'],'unknown')
    @unittest.skipIf(P.cv2 is None,'OpenCV optical flow not installed')
    def test_expanding_view_raises_looming_warning(self):
        m=PerceptionMonitor()
        frames=[zoom(TEX,1+0.06*i) for i in range(6)]
        for i,f in enumerate(frames): m.push_frame(f,i*0.05)
        s=m.snapshot(0.26)
        self.assertIn('looming',[w['kind'] for w in s['warnings']]);self.assertEqual(s['obstacle_state'],'warning')
        self.assertGreater(s['flow']['divergence'],0)
    def test_extra_detector_can_only_add_warnings(self):
        m=PerceptionMonitor(extra_detectors=[lambda g:[{'kind':'person','region':'center','confidence':0.9}]])
        for i in range(4): m.push_frame(np.roll(TEX,i,axis=1),i*0.05)
        s=m.snapshot(0.16);self.assertEqual(s['obstacle_state'],'warning');self.assertIn('unknown',[w['kind'] for w in s['warnings']])
    def test_uncommanded_motion_warning(self):
        m=PerceptionMonitor()
        for i in range(4): m.push_frame(np.roll(TEX,i*10,axis=1),i*0.05)
        self.assertIn('unexpected_motion',[w['kind'] for w in m.snapshot(0.16)['warnings']])
        m2=PerceptionMonitor();m2.set_commanding(True)
        for i in range(4): m2.push_frame(np.roll(TEX,i*10,axis=1),i*0.05)
        self.assertNotIn('unexpected_motion',[w['kind'] for w in m2.snapshot(0.16)['warnings']])

if __name__=='__main__': unittest.main()