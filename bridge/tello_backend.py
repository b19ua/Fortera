"""DJI Tello backend for the reference Fortera bridge (protocol §11–14). Companion process only.

UDP SDK: commands to <host>:8889 (one in flight, replies are unlabelled "ok"/"error"/value), state from :8890,
H.264 video on :11111 decoded locally for warning-only camera checks. Facts NOT verified on an aircraft:
`stop` support on a standard Tello and whether it interrupts a running move (we also send `rc 0 0 0 0`),
vgx/vgy units (assumed dm/s), tof semantics, and mission pad behaviour on Tello EDU.
"""
import asyncio, math, time
from policy import preflight, resolve_profile, TELLO_CAPABILITIES
from perception import PerceptionMonitor, H264Reader
from tello_estimate import EstimateTracker

def parse_state(text):
    out={}
    for part in text.strip().strip(';').split(';'):
        if ':' not in part: continue
        k,v=part.split(':',1)
        if k=='mpry': continue
        try: out[k]=float(v) if '.' in v else int(v)
        except ValueError: pass
    return out

class _Cmd(asyncio.DatagramProtocol):
    def __init__(self): self.queue=asyncio.Queue()
    def datagram_received(self,data,addr): self.queue.put_nowait(data.decode('utf-8','replace').strip())

class _State(asyncio.DatagramProtocol):
    def __init__(self,cb): self.cb=cb
    def datagram_received(self,data,addr): self.cb(parse_state(data.decode('ascii','replace')))

class TelloBackend:
    mode='tello';telemetry_interval=0.1
    def __init__(self,host='192.168.10.1',cmd_port=8889,state_port=8890,profile=None,mission_pads=False,corridor=None,video=True,
                 video_url='udp://0.0.0.0:11111?overrun_nonfatal=1&fifo_size=50000000',land_after_s=8.0,local_cmd_port=0,monitor=None):
        self.host=host;self.cmd_port=cmd_port;self.state_port=state_port;self.local_cmd_port=local_cmd_port
        self.profile=profile or resolve_profile('indoor_demo');L=self.profile['limits']
        self.mission_pads=mission_pads;self.corridor=list(corridor or [])
        self.monitor=monitor or PerceptionMonitor(stale_ms=L['frame_max_age_ms'])
        self.reader=H264Reader(self.monitor,video_url) if video else None
        self.estimate=EstimateTracker()
        self.vehicles={'T01':dict(id='T01',backend='tello',lat=0,lon=0,relative_alt_m=0,battery_pct=0,armed=False,ready=False,mode='CONNECTING',capabilities=list(TELLO_CAPABILITIES))}
        self.waypoints=[];self.landing_radius_m=12;self.min_clearance_m=10
        self.state={};self.state_at=None;self.model='unknown';self.sdk_version=None;self.wifi_snr=None;self.supports_stop=None
        self.lock=asyncio.Lock();self.proto=None;self.transport=None;self.last_cmd_at=0.0;self.flying=False;self.last_step_done=0.0
        self.land_after_s=land_after_s;self.watch_since=None;self.watch_landed=False;self.tasks=[];self.connected=False
    # ---------- SDK transport ----------
    async def start(self):
        loop=asyncio.get_running_loop()
        self.transport,self.proto=await loop.create_datagram_endpoint(_Cmd,local_addr=('0.0.0.0',self.local_cmd_port),remote_addr=(self.host,self.cmd_port))
        self.state_transport,_=await loop.create_datagram_endpoint(lambda:_State(self.on_state),local_addr=('0.0.0.0',self.state_port))
        if (await self.send('command',5))!='ok': raise RuntimeError('Tello did not enter SDK mode')
        self.connected=True
        v=await self.query('sdk?');self.sdk_version=v if v and v.replace('.','').isdigit() else None
        if self.mission_pads:
            if (await self.query('mon'))=='ok' and (await self.query('mdirection 0'))=='ok':
                self.model='tello_edu';self.vehicles['T01']['capabilities'].append('mission_pad')
        else: self.model='tello' if self.sdk_version is None else 'unknown'
        await self.query(f"speed {int(self.profile['limits']['speed_cm_s'])}")
        if self.reader: await self.query('streamon');self.reader.start()
        self.tasks.append(asyncio.create_task(self.keepalive()))
    async def send(self,cmd,timeout):
        async with self.lock:
            while not self.proto.queue.empty(): self.proto.queue.get_nowait()  # drop late replies of earlier commands
            self.transport.sendto(cmd.encode());self.last_cmd_at=time.monotonic()
            return await asyncio.wait_for(self.proto.queue.get(),timeout)
    async def query(self,cmd,timeout=3):
        try: return await self.send(cmd,timeout)
        except asyncio.TimeoutError: return None
    def send_now(self,cmd):
        """Priority path for hold/land: never waits behind the in-flight command."""
        if self.transport: self.transport.sendto(cmd.encode());self.last_cmd_at=time.monotonic()
    async def keepalive(self):
        """Tello lands by itself after ~15 s without commands; a light query keeps the link and refreshes Wi-Fi SNR."""
        n=0
        while True:
            await asyncio.sleep(1)
            if time.monotonic()-self.last_cmd_at>5 and not self.lock.locked():
                n+=1;r=await self.query('wifi?' if n%2 else 'battery?',2)
                if r and n%2 and r.isdigit(): self.wifi_snr=int(r)
    def on_state(self,d): self.state=d;self.state_at=time.monotonic()
    # ---------- telemetry ----------
    def airborne(self): return self.flying or self.state.get('h',0)>20
    def refresh(self):
        now=time.monotonic();s=self.state;age=(now-self.state_at) if self.state_at else 1e6;v=self.vehicles['T01'];air=self.airborne()
        perception=self.monitor.snapshot(now)
        self.estimate.observe_state(s,age,air,now)
        tof=s.get('tof');h=s.get('h',0)
        if not air or tof is None: vps='unknown'
        elif tof<=10 or tof>=6000 or perception['frame_status']!='ok' or any(w['kind']=='low_light' for w in perception['warnings']): vps='degraded'
        else: vps='likely_ok'
        v.update(relative_alt_m=round(h/100,2),battery_pct=max(0,min(100,s.get('bat',0))),armed=air,ready=self.connected and age<1,
                 mode='FLYING' if air else ('GROUND' if self.connected else 'CONNECTING'),ground_speed_mps=round(math.hypot(s.get('vgx',0),s.get('vgy',0))/10,2),
                 sdk_state=dict(model=self.model,sdk_version=self.sdk_version,state_age_ms=round(min(age,1e6)*1000),height_cm=h,tof_cm=tof,baro_m=s.get('baro'),
                                pitch_deg=s.get('pitch',0),roll_deg=s.get('roll',0),yaw_deg=s.get('yaw',0),vgx=s.get('vgx',0),vgy=s.get('vgy',0),vgz=s.get('vgz',0),
                                temp_high_c=s.get('temph'),flight_time_s=s.get('time'),wifi_snr=self.wifi_snr,vps_hint=vps),
                 perception=perception,estimate=self.estimate.snapshot())
        if self.estimate.valid: v.update(local_x_m=round(self.estimate.x,3),local_y_m=round(self.estimate.y,3))
        else: v.pop('local_x_m',None);v.pop('local_y_m',None)
        if self.model=='tello_edu':
            v['mission_pad']=dict(enabled=True,direction='down',mid=int(s.get('mid',-1)),x_cm=s.get('x',0),y_cm=s.get('y',0),z_cm=s.get('z',0),age_ms=round(min(age,1e6)*1000),corridor=self.corridor)
    # ---------- commands ----------
    def timeout_for(self,a):
        sp=self.profile['limits']['speed_cm_s'];k=a['type']
        if k=='hold': return 3
        if k in ('takeoff','land'): return 25+(max(0,a.get('altitude_m',0)-1)*100/sp if k=='takeoff' else 0)
        if k=='navigate': return 8+math.hypot(a['x_m'],a['y_m'])*100/sp+abs(math.degrees(a['yaw_rad']))/30
        if k=='return_home': return 12+(abs(self.estimate.x)+abs(self.estimate.y))*100/sp
        if k=='pad_step': return 25
        return 10
    async def _ok(self,cmd,timeout):
        r=await self.send(cmd,timeout)
        if r!='ok': raise ValueError(f'Tello replied {r!r} to {cmd.split()[0]}')
    async def _axis(self,value_m,pos,neg,timeout):
        cm=int(round(abs(value_m)*100))
        if cm<20: return
        while cm>0:
            step=min(cm,500)
            if step<20: break
            await self._ok(f"{pos if value_m>0 else neg} {step}",timeout);cm-=step
    async def execute(self,vid,a):
        self.refresh();v=self.vehicles[vid]
        a=preflight(v,a,self.waypoints,self.landing_radius_m,self.min_clearance_m,self.profile)
        if a['type']=='hold': return await self.hold()
        now=time.monotonic();L=self.profile['limits']
        if a['type'] in ('navigate','pad_step') and now-self.last_step_done<L['settle_ms']/1000: raise ValueError('Wait for the hover to settle before the next step')
        t=self.timeout_for(a);self.estimate.begin_command();self.monitor.set_commanding(True)
        try:
            if a['type']=='takeoff':
                await self._ok('takeoff',t);self.flying=True;self.estimate.reset_at_takeoff(self.state.get('yaw',0))
                if self.profile['id']=='standard' and a['altitude_m']>1.2: await self._ok(f"up {int(min(500,round((a['altitude_m']-1)*100)))}",t)
            elif a['type']=='land':
                await self._ok('land',t);self.flying=False;self.estimate.invalidate('not_airborne')
            elif a['type']=='navigate':
                await self._axis(a['x_m'],'forward','back',t);await self._axis(a['y_m'],'left','right',t)
                deg=int(round(abs(math.degrees(a['yaw_rad']))))
                if deg>=1: await self._ok(f"{'ccw' if a['yaw_rad']>0 else 'cw'} {deg}",t)
                self.estimate.apply_step(a['x_m'],a['y_m'],a['yaw_rad'])
            elif a['type']=='return_home':
                h=math.radians(self.estimate.yaw);wx,wy=-self.estimate.x,-self.estimate.y
                await self._axis(math.cos(h)*wx+math.sin(h)*wy,'forward','back',t);await self._axis(-math.sin(h)*wx+math.cos(h)*wy,'left','right',t)
                self.estimate.returned_home()
            elif a['type']=='pad_step':
                await self._ok(f"go {a['x_cm']} {a['y_cm']} {a['z_cm']} {int(L['speed_cm_s'])} m{a['mid']}",t)
        except asyncio.CancelledError:
            self.estimate.invalidate('interrupted');raise
        except Exception:
            self.estimate.invalidate('sdk_error');raise
        finally:
            self.estimate.end_command(time.monotonic());self.monitor.set_commanding(False);self.last_step_done=time.monotonic()
    async def hold(self):
        """Best-effort hover: `stop` (SDK 2.0) then `rc 0 0 0 0`. Hover quality depends on the downward vision sensors."""
        if self.airborne(): self.estimate.invalidate('stop')
        self.send_now('stop');await asyncio.sleep(0.05);self.send_now('rc 0 0 0 0')
    def on_session_start(self): self.estimate.invalidate('reconnect')
    def hold_reason(self,now=None):
        now=time.monotonic() if now is None else now
        if not self.airborne(): return None
        if not self.state_at or now-self.state_at>1: return 'telemetry_lost'
        p=self.monitor.snapshot(now)
        if p['frame_status']!='ok': return f"video_{p['frame_status']}"
        if p['frame_age_ms']>2*self.profile['limits']['frame_max_age_ms']: return 'video_stale'
        if p['confidence'] is None: return 'camera_confidence_unknown'
        return None
    async def watchdog(self,enabled=True):
        """Local watchdog (no browser needed): hold on stale/black/lost video, unknown confidence or lost state; land if it persists."""
        reason=self.hold_reason()
        v=self.vehicles['T01']
        if not reason:
            self.watch_since=None;self.watch_landed=False
            if v.get('safety_reason','').startswith('watchdog:'): v.pop('safety_reason',None)
            return None
        v['safety_reason']=f'watchdog:{reason}'
        if not enabled: return reason
        now=time.monotonic()
        if self.watch_since is None: self.watch_since=now;await self.hold()
        elif not self.watch_landed and now-self.watch_since>self.land_after_s: self.watch_landed=True;self.send_now('land');self.flying=False
        return reason