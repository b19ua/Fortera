"""Local, lightweight camera checks for the Tello video (protocol §12). WARNING-ONLY.

This is not obstacle avoidance and not a depth sensor. A monocular camera cannot clear a path, so the output has
no "clear" state: the best it reports is `no_warning`. Stale, black, frozen or lost frames and unknown confidence are
reported so the bridge and browser can block new movement and request a hold. Any optional detector added through
`extra_detectors` (for example an ONNX model) may only ADD warnings, never remove them.
"""
import math, threading, time
import numpy as np
try:
    import cv2
except ImportError:  # numpy fallback: freshness, black/frozen, brightness, sharpness; no optical flow
    cv2=None

REGIONS=('left','center','right')
MIN_CORNER_EIG=1e-4

def _gray_small(frame,size):
    if frame.ndim==3: frame=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY) if cv2 is not None else frame.mean(axis=2)
    if cv2 is not None: return cv2.resize(frame.astype(np.uint8),size,interpolation=cv2.INTER_AREA)
    h,w=frame.shape;sy=max(1,h//size[1]);sx=max(1,w//size[0])
    return frame[::sy,::sx][:size[1],:size[0]].astype(np.uint8)

def _laplacian_var(g):
    if cv2 is not None: return float(cv2.Laplacian(g,cv2.CV_64F).var())
    f=g.astype(np.float64);lap=-4*f[1:-1,1:-1]+f[:-2,1:-1]+f[2:,1:-1]+f[1:-1,:-2]+f[1:-1,2:]
    return float(lap.var())

def _features(g):
    if cv2 is None:
        gy,gx=np.gradient(g.astype(np.float64));mag=np.hypot(gx,gy)
        ys,xs=np.nonzero(mag[::8,::8]>40);return np.stack([xs*8,ys*8],axis=1).astype(np.float32)
    # Absolute corner strength on a blurred image, so sensor noise on a blank wall does not count as texture.
    b=cv2.GaussianBlur(g,(5,5),0);eig=cv2.cornerMinEigenVal(b,3)
    pts=cv2.goodFeaturesToTrack(b,maxCorners=200,qualityLevel=0.01,minDistance=7)
    if pts is None: return np.zeros((0,2),np.float32)
    pts=pts.reshape(-1,2);keep=eig[pts[:,1].astype(int).clip(0,g.shape[0]-1),pts[:,0].astype(int).clip(0,g.shape[1]-1)]>MIN_CORNER_EIG
    return pts[keep]

class PerceptionMonitor:
    def __init__(self,size=(320,240),black_mean=12,black_std=6,frozen_diff=0.3,frozen_frames=15,blur_var=25,
                 low_texture=24,low_light=40,looming_div=0.035,motion_px=6,stale_ms=500,lost_ms=2000,extra_detectors=()):
        self.size=size;self.black_mean=black_mean;self.black_std=black_std;self.frozen_diff=frozen_diff;self.frozen_frames=frozen_frames
        self.blur_var=blur_var;self.low_texture=low_texture;self.low_light=low_light;self.looming_div=looming_div;self.motion_px=motion_px
        self.stale_ms=stale_ms;self.lost_ms=lost_ms;self.extra=list(extra_detectors)
        self.lock=threading.Lock();self.started=None;self.seq=0;self.last_frame_at=None;self.last_analysis_at=None
        self.fps=0.0;self.prev=None;self.prev_pts=None;self.same=0;self.result=None;self.commanding=False
    def start(self,now=None): self.started=time.monotonic() if now is None else now
    def set_commanding(self,on): self.commanding=bool(on)

    def push_frame(self,frame,now=None):
        now=time.monotonic() if now is None else now
        g=_gray_small(np.asarray(frame),self.size)
        with self.lock:
            if self.last_frame_at is not None:
                dt=max(1e-3,now-self.last_frame_at);self.fps=0.8*self.fps+0.2*(1/dt) if self.fps else 1/dt
            self.last_frame_at=now;self.seq+=1
            self.result=self._analyse(g);self.last_analysis_at=now

    def _analyse(self,g):
        mean=float(g.mean());std=float(g.std());sharp=_laplacian_var(g)
        black=mean<self.black_mean and std<self.black_std
        if self.prev is not None and float(np.abs(g.astype(np.int16)-self.prev.astype(np.int16)).mean())<self.frozen_diff: self.same+=1
        else: self.same=0
        frozen=self.same>=self.frozen_frames
        pts=_features(g);w=g.shape[1]
        region_pts={r:int(((pts[:,0]>=i*w/3)&(pts[:,0]<(i+1)*w/3)).sum()) if len(pts) else 0 for i,r in enumerate(REGIONS)}
        flow=None;region_div={r:0.0 for r in REGIONS}
        if cv2 is not None and self.prev is not None and self.prev_pts is not None and len(self.prev_pts)>=8 and not black:
            nxt,st,_=cv2.calcOpticalFlowPyrLK(self.prev,g,self.prev_pts.reshape(-1,1,2),None,winSize=(15,15),maxLevel=2)
            ok=st.reshape(-1)==1
            if ok.sum()>=8:
                p0=self.prev_pts[ok];d=nxt.reshape(-1,2)[ok]-p0
                mag=np.hypot(d[:,0],d[:,1]);med=np.median(d,axis=0);medmag=float(np.hypot(*med))
                if medmag>0.5:
                    cosang=(d@med)/(np.maximum(mag,1e-6)*medmag);consistency=float((cosang>math.cos(math.radians(45))).mean())
                else: consistency=float((mag<1.5).mean())
                c=np.array([g.shape[1]/2,g.shape[0]/2]);r=p0-c;rr=np.maximum((r*r).sum(axis=1),25.0)
                radial=((r*(d-med)).sum(axis=1))/rr  # expansion rate per point after removing translation
                div=float(np.median(radial))
                for i,name in enumerate(REGIONS):
                    m=(p0[:,0]>=i*w/3)&(p0[:,0]<(i+1)*w/3)
                    region_div[name]=float(np.median(radial[m])) if m.sum()>=4 else 0.0
                flow=dict(median_px=round(float(np.median(mag)),2),divergence=round(div,4),consistency=round(min(1.0,max(0.0,consistency)),2))
        self.prev=g;self.prev_pts=pts if len(pts) else None
        warnings=[]
        if mean<self.low_light and not black: warnings.append(dict(kind='low_light',region='full',confidence=round(min(1,(self.low_light-mean)/self.low_light+0.3),2)))
        if sharp<self.blur_var and not black: warnings.append(dict(kind='blur',region='full',confidence=0.6))
        for r,n in region_pts.items():
            if n<self.low_texture/3: warnings.append(dict(kind='low_texture',region=r,confidence=0.5))
            if region_div[r]>self.looming_div: warnings.append(dict(kind='looming',region=r,confidence=round(min(1.0,region_div[r]/(2*self.looming_div)),2)))
        if flow and flow['median_px']>self.motion_px and not self.commanding: warnings.append(dict(kind='unexpected_motion',region='full',confidence=0.6))
        for detect in self.extra:
            try: warnings.extend(dict(kind='unknown' if w.get('kind') not in ('looming','low_texture','low_light','blur','frozen','unexpected_motion') else w['kind'],region=w.get('region','full') if w.get('region') in ('left','center','right','full') else 'full',confidence=float(min(1,max(0,w.get('confidence',0.5))))) for w in detect(g))
            except Exception: warnings.append(dict(kind='unknown',region='full',confidence=0.5))
        total=sum(region_pts.values())
        if black or frozen: confidence=None
        elif self.seq<=1 and cv2 is not None: confidence=None  # flow needs two frames
        else:
            confidence=min(1.0,total/100)*(0.5 if sharp<self.blur_var else 1)*(0.5 if mean<self.low_light else 1)*(flow['consistency'] if flow and flow['median_px']>0.5 else 1)
            confidence=round(confidence,2)
        return dict(status='black' if black else 'frozen' if frozen else 'ok',brightness=round(mean,1),sharpness=round(sharp,1),texture_points=total,flow=flow,warnings=warnings[:8],confidence=confidence,center_texture=region_pts['center'])

    def snapshot(self,now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            r=self.result;age=(now-self.last_frame_at)*1000 if self.last_frame_at is not None else None
            out=dict(source='bridge_cv',warning_only=True,frame_seq=self.seq,frame_age_ms=round(age if age is not None else self.lost_ms*10),video_fps=round(self.fps,1),
                     updated_ms_ago=round((now-self.last_analysis_at)*1000) if self.last_analysis_at is not None else self.lost_ms*10,
                     brightness=None,sharpness=None,texture_points=None,flow=None,warnings=[],confidence=None)
            if age is None: out.update(frame_status='decoding' if self.started is not None and now-self.started<self.lost_ms/1000 else 'lost',obstacle_state='unknown');return out
            out.update(brightness=r['brightness'],sharpness=r['sharpness'],texture_points=r['texture_points'],flow=r['flow'],warnings=list(r['warnings']))
            status='lost' if age>self.lost_ms else 'stale' if age>self.stale_ms else r['status']
            out['frame_status']=status
            if status=='frozen': out['warnings']=(out['warnings']+[dict(kind='frozen',region='full',confidence=0.9)])[:8]
            out['confidence']=r['confidence'] if status=='ok' else None
            if out['confidence'] is None: out['obstacle_state']='unknown'
            elif any(w['kind'] in ('looming','unknown') for w in out['warnings']): out['obstacle_state']='warning'
            elif out['confidence']<0.3 or r['center_texture']<self.low_texture/3: out['obstacle_state']='unknown'
            elif out['warnings']: out['obstacle_state']='warning'
            else: out['obstacle_state']='no_warning'
            return out

class H264Reader(threading.Thread):
    """Decodes the Tello H.264 stream (UDP 11111) with OpenCV/FFmpeg, feeds the monitor and keeps the latest frame."""
    def __init__(self,monitor,url='udp://0.0.0.0:11111?overrun_nonfatal=1&fifo_size=50000000',analysis_hz=15):
        super().__init__(daemon=True);self.monitor=monitor;self.url=url;self.period=1/analysis_hz;self.running=True
        self.lock=threading.Lock();self.latest=None;self.latest_seq=0;self.latest_at=None;self._last_analysis=0.0
    def run(self):
        if cv2 is None: return
        self.monitor.start()
        while self.running:
            cap=cv2.VideoCapture(self.url,cv2.CAP_FFMPEG)
            while self.running and cap.isOpened():
                ok,frame=cap.read()
                if not ok: break
                now=time.monotonic()
                with self.lock: self.latest=frame;self.latest_seq+=1;self.latest_at=now
                if now-self._last_analysis>=self.period: self._last_analysis=now;self.monitor.push_frame(frame,now)
            cap.release();time.sleep(0.5)
    def latest_frame(self):
        with self.lock: return self.latest,self.latest_seq,self.latest_at
    def stop(self): self.running=False