"""Fortera reference bridge. External companion process; bench mode by default."""
import asyncio, collections, hmac, json, math, os, secrets, ssl, time
from websockets.asyncio.server import serve
from backends import BenchBackend, MavsdkBackend, Ros2Backend
from policy import preflight_plan, load_waypoints, landing_radius, resolve_profile
from obstacles import sensing_reason
VERSION='fortera.bridge/2'


def validate_command(msg,vehicles,now=None,waypoints=None,radius=12,minimum=10,profile=None):
    now=now or time.time()*1000
    if msg.get('v')!=VERSION or msg.get('type')!='command': raise ValueError('Unsupported protocol')
    if not isinstance(msg.get('id'),str) or not 1<=len(msg['id'])<=64: raise ValueError('Invalid command id')
    deadline=msg.get('deadline_ms')
    if isinstance(deadline,bool) or not isinstance(deadline,(int,float)) or not math.isfinite(deadline) or not now<=deadline<=now+5000: raise ValueError('Expired or invalid command deadline')
    vid=msg.get('vehicle');v=vehicles.get(vid)
    if not v: raise ValueError('Unknown vehicle')
    raw=msg.get('actions') if 'actions' in msg else [msg.get('action')]
    if 'actions' in msg and 'action' in msg: raise ValueError('Use action or actions, not both')
    if not isinstance(raw,list) or not 1<=len(raw)<=32: raise ValueError('Plan must contain 1–32 actions')
    actions=preflight_plan(v,raw,waypoints or [],radius,minimum,profile)
    return vid,actions if 'actions' in msg else actions[0]

class Gateway:
    def __init__(self,backend,token,origins,enabled=False,waypoints=None,radius=12,minimum=10):
        self.backend=backend;self.token=token;self.origins=origins;self.enabled=enabled;self.owner=None
        self.waypoints=load_waypoints(json.dumps(waypoints or []));self.landing_radius_m=landing_radius(radius)
        backend.waypoints=self.waypoints;backend.landing_radius_m=self.landing_radius_m
        self.min_clearance_m=landing_radius(minimum);backend.min_clearance_m=self.min_clearance_m
        self.running={};self.stale_held=set();self.safety_lock=asyncio.Lock()
    async def enforce_obstacle_safety(self):
        async with self.safety_lock:
            for vid,v in self.backend.vehicles.items():
                data=v.get('obstacle_sensing')
                if data is not None:
                    v['capabilities']=[c for c in v['capabilities'] if c!='obstacle_sensing']
                    if data.get('healthy') and data.get('updated_ms_ago',1000)<1000:v['capabilities'].append('obstacle_sensing')
                stale=sensing_reason(v,self.min_clearance_m)=='obstacle_data_stale'
                if not stale:
                    self.stale_held.discard(vid)
                    if v.get('safety_reason')=='obstacle_data_stale':v.pop('safety_reason',None)
                    continue
                if not v.get('armed') or v.get('relative_alt_m',0)<=0.6:continue
                v['safety_reason']='obstacle_data_stale'
                if vid in self.stale_held or (self.backend.mode!='bench' and not self.enabled):continue
                pending=self.running.get(vid)
                if pending:pending.cancel();await asyncio.gather(pending,return_exceptions=True)
                try:
                    await asyncio.wait_for(self.backend.execute(vid,{'type':'hold'}),2)
                    self.stale_held.add(vid)
                except Exception:v['safety_reason']='obstacle_data_stale'
    async def monitor_obstacles(self):
        while True:
            if hasattr(self.backend,'refresh'):self.backend.refresh()
            await self.enforce_obstacle_safety()
            # Tello: local camera/state watchdog. Acts only when hardware commands are enabled locally.
            if hasattr(self.backend,'watchdog'):
                try: await self.backend.watchdog(self.backend.mode=='bench' or self.enabled)
                except Exception: pass
            await asyncio.sleep(.1)
    async def handler(self,ws):
        origin=ws.request.headers.get('Origin')
        if origin not in self.origins: await ws.close(1008,'Origin not allowed');return
        try:
            hello=json.loads(await asyncio.wait_for(ws.recv(),5))
            if not isinstance(hello,dict) or hello.get('v')!=VERSION or hello.get('type')!='hello' or not isinstance(hello.get('token'),str) or not hmac.compare_digest(hello['token'],self.token): raise ValueError()
        except Exception: await ws.close(1008,'Pairing rejected');return
        if self.owner is not None: await ws.close(1008,'Controller lease busy');return
        self.owner=ws;last_ping=time.monotonic();seq=0;cache={};running=self.running;rate=collections.deque()
        if hasattr(self.backend,'on_session_start'):self.backend.on_session_start()
        profile=getattr(self.backend,'profile',None)
        async def send(msg): await ws.send(json.dumps(dict(v=VERSION,**msg),allow_nan=False))
        await send(dict(type='welcome',session=secrets.token_hex(16),mode=self.backend.mode,waypoints=self.waypoints,landing_radius_m=self.landing_radius_m,min_clearance_m=self.min_clearance_m,**({'video':self.video} if getattr(self,'video',None) else {}),**({'profile':{'id':profile['id'],'limits':profile['limits']}} if profile else {})))
        async def telemetry():
            nonlocal seq
            while True:
                if time.monotonic()-last_ping>5: await ws.close(1008,'Heartbeat lost');return
                if hasattr(self.backend,'refresh'): self.backend.refresh()
                await self.enforce_obstacle_safety()
                await send(dict(type='telemetry',seq=seq,vehicles=list(self.backend.vehicles.values()),waypoints=self.waypoints,landing_radius_m=self.landing_radius_m,min_clearance_m=self.min_clearance_m));seq+=1;await asyncio.sleep(getattr(self.backend,'telemetry_interval',.2))
        ticker=asyncio.create_task(telemetry())
        async def execute(msg,vid,a):
            cid=msg['id']
            try:
                for action in (a if isinstance(a,list) else [a]):
                    # Per-action timeout: a slow Tello step must not be reported failed while it is still flying.
                    await asyncio.wait_for(self.backend.execute(vid,action),getattr(self.backend,'timeout_for',lambda _a:10)(action))
                ack=dict(type='ack',id=cid,status='completed',reason='Backend acknowledged request; arrival is not asserted')
            except asyncio.CancelledError:
                ack=dict(type='ack',id=cid,status='failed',reason='Preempted by hold or disconnect')
            except Exception as e: ack=dict(type='ack',id=cid,status='failed',reason=str(e)[:200])
            cache[cid]=(msg,ack)
            if running.get(vid) is asyncio.current_task(): running.pop(vid,None)
            try: await send(ack)
            except Exception: pass
        try:
            async for raw in ws:
                try: msg=json.loads(raw)
                except Exception: await ws.close(1008,'Invalid JSON');break
                if not isinstance(msg,dict): await ws.close(1008,'Invalid envelope');break
                if msg.get('v')==VERSION and msg.get('type')=='ping': last_ping=time.monotonic();await send(dict(type='pong'));continue
                cid=msg.get('id','invalid')
                try:
                    if cid in cache:
                        previous,ack=cache[cid]
                        if previous!=msg: raise ValueError('Command id reused with different content')
                        await send(ack);continue
                    if hasattr(self.backend,'refresh'):self.backend.refresh()
                    vid,a=validate_command(msg,self.backend.vehicles,waypoints=self.waypoints,radius=self.landing_radius_m,minimum=self.min_clearance_m,profile=profile)
                    if len(cache)>=1000: raise ValueError('Session command limit reached; reconnect explicitly')
                    if self.backend.mode!='bench' and not self.enabled: raise ValueError('Hardware commands disabled locally')
                    hold_only=all(action['type']=='hold' for action in (a if isinstance(a,list) else [a]))
                    if not hold_only:
                        now=time.monotonic()
                        while rate and now-rate[0]>1:rate.popleft()
                        if len(rate)>=3: raise ValueError('At most three plans per second')
                        if vid in running: raise ValueError('Vehicle already has a pending request')
                        rate.append(now)
                    elif vid in running: running[vid].cancel();await asyncio.gather(running[vid],return_exceptions=True)
                    ack=dict(type='ack',id=cid,status='accepted');cache[cid]=(msg,ack);await send(ack)
                    running[vid]=asyncio.create_task(execute(msg,vid,a))
                except (ValueError,TypeError) as e: await send(dict(type='ack',id=str(cid)[:64],status='rejected',reason=str(e)))
        finally:
            ticker.cancel()
            for task in list(running.values()):task.cancel()
            await asyncio.gather(ticker,*list(running.values()),return_exceptions=True)
            await self.backend.hold();self.owner=None

def video_source(kind,url,frame_url=''):
    """Optional camera stream shown by the browser. Display only; never a command channel."""
    if not url: return None
    from urllib.parse import urlparse
    u=urlparse(url);loopback=u.hostname in ('localhost','127.0.0.1','::1')
    if kind not in ('whep','mjpeg'): raise SystemExit('FORTERA_VIDEO_KIND must be whep or mjpeg')
    if u.username or u.password or not (u.scheme=='https' or (u.scheme=='http' and loopback)): raise SystemExit('FORTERA_VIDEO_URL must be HTTPS (plain HTTP only on loopback) without credentials')
    out=dict(kind=kind,url=url)
    if frame_url:
        f=urlparse(frame_url)
        if f.username or f.password or not (f.scheme=='https' or (f.scheme=='http' and f.hostname in ('localhost','127.0.0.1','::1'))): raise SystemExit('FORTERA_VIDEO_FRAME_URL must be HTTPS (plain HTTP only on loopback) without credentials')
        out['frame_url']=frame_url
    return out

def tello_backend_from_env(env):
    from tello_backend import TelloBackend
    overrides=json.loads(env.get('FORTERA_TELLO_LIMITS','{}'))
    if not isinstance(overrides,dict): raise SystemExit('FORTERA_TELLO_LIMITS must be a JSON object (limits may only be tightened)')
    profile_id=env.get('FORTERA_TELLO_PROFILE','indoor_demo')
    if profile_id not in ('indoor_demo','standard'): raise SystemExit('FORTERA_TELLO_PROFILE must be indoor_demo or standard')
    corridor=json.loads(env.get('FORTERA_TELLO_CORRIDOR','[]'))
    if not isinstance(corridor,list) or len(corridor)>16 or any(not isinstance(c,dict) or set(c)!={'from','to'} or not all(isinstance(c[k],int) and 1<=c[k]<=8 for k in c) for c in corridor): raise SystemExit('FORTERA_TELLO_CORRIDOR must list at most 16 {"from":1-8,"to":1-8} hops')
    return TelloBackend(host=env.get('FORTERA_TELLO_HOST','192.168.10.1'),profile=resolve_profile(profile_id,overrides),mission_pads=env.get('FORTERA_TELLO_MISSION_PADS')=='1',corridor=corridor,video=env.get('FORTERA_TELLO_VIDEO','1')=='1')

async def main():
    token=os.environ.get('FORTERA_BRIDGE_TOKEN','');origins=set(os.environ.get('FORTERA_ALLOWED_ORIGINS','http://localhost:8080').split(','))
    if len(token)<24: raise SystemExit('Set a private FORTERA_BRIDGE_TOKEN of at least 24 characters')
    mode=os.environ.get('FORTERA_BACKEND','bench');count=int(os.environ.get('FORTERA_BENCH_COUNT','5'))
    if not 1<=count<=20: raise SystemExit('Bench fleet must contain 1–20 vehicles')
    backend=BenchBackend(count) if mode=='bench' else MavsdkBackend(json.loads(os.environ.get('FORTERA_MAVSDK_ENDPOINTS','{"F01":"udpin://0.0.0.0:14540"}'))) if mode=='mavsdk' else Ros2Backend() if mode=='ros2' else tello_backend_from_env(os.environ) if mode=='tello' else None
    if backend is None: raise SystemExit('Unknown backend')
    if not 1<=len(backend.vehicles)<=20: raise SystemExit('Fleet must contain 1–20 vehicles')
    tls=None
    if os.environ.get('FORTERA_TLS_CERT'):
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(os.environ['FORTERA_TLS_CERT'],os.environ['FORTERA_TLS_KEY'])
    host=os.environ.get('FORTERA_BIND','127.0.0.1')
    if host not in ('127.0.0.1','::1','localhost') and tls is None: raise SystemExit('Non-loopback bridges require TLS')
    points=load_waypoints(os.environ.get('FORTERA_WAYPOINTS','[]'));radius=landing_radius(os.environ.get('FORTERA_LANDING_RADIUS_M','12'))
    minimum=landing_radius(os.environ.get('FORTERA_MIN_CLEARANCE_M','10'))
    video=video_source(os.environ.get('FORTERA_VIDEO_KIND',''),os.environ.get('FORTERA_VIDEO_URL',''),os.environ.get('FORTERA_VIDEO_FRAME_URL',''))
    video_server=None
    if mode=='tello' and getattr(backend,'reader',None):
        from video_server import VideoServer
        vport=int(os.environ.get('FORTERA_VIDEO_PORT','8766'))
        video_server=VideoServer(backend.reader,origins,port=vport)
        video=video or dict(kind='mjpeg',url=f'http://127.0.0.1:{vport}/video',frame_url=f'http://127.0.0.1:{vport}/frame.jpg')
    await backend.start();gateway=Gateway(backend,token,origins,os.environ.get('FORTERA_ENABLE_HARDWARE')=='1',points,radius,minimum);gateway.video=video
    monitor=asyncio.create_task(gateway.monitor_obstacles())
    if video_server: await video_server.start()

    # Authenticated loopback-only shutdown endpoint used by the Windows GUI.
    # It is deliberately separate from the browser/flight protocol.
    control_token=os.environ.get('FORTERA_CONTROL_TOKEN','')
    stop_event=asyncio.Event()
    async def control_handler(reader,writer):
        try:
            head=await asyncio.wait_for(reader.readuntil(b'\\r\\n\\r\\n'),2)
            if len(head)>8192: raise ValueError('oversized control request')
            lines=head.decode('latin-1').split('\\r\\n')
            bits=lines[0].split(' ')
            headers={k.strip().lower():v.strip() for k,v in (line.split(':',1) for line in lines[1:] if ':' in line)}
            ok=(len(bits)==3 and bits[0]=='POST' and bits[1]=='/shutdown'
                and len(control_token)>=24
                and hmac.compare_digest(headers.get('x-fortera-control-token',''),control_token))
            status='202 Accepted' if ok else '403 Forbidden'
            body=b'Bridge shutdown accepted' if ok else b'Forbidden'
            writer.write((f'HTTP/1.1 {status}\\r\\nContent-Length: {len(body)}\\r\\nConnection: close\\r\\n\\r\\n').encode()+body)
            await writer.drain()
            if ok: stop_event.set()
        except Exception:
            try:
                writer.write(b'HTTP/1.1 400 Bad Request\\r\\nContent-Length: 0\\r\\nConnection: close\\r\\n\\r\\n')
                await writer.drain()
            except Exception: pass
        finally:
            writer.close()
            try: await writer.wait_closed()
            except Exception: pass
    control_server=None
    if control_token:
        if len(control_token)<24: raise SystemExit('FORTERA_CONTROL_TOKEN must be at least 24 characters')
        control_server=await asyncio.start_server(control_handler,'127.0.0.1',int(os.environ.get('FORTERA_CONTROL_PORT','8767')))
    ws_server=await serve(gateway.handler,host,int(os.environ.get('FORTERA_PORT','8765')),ssl=tls,max_size=8192,ping_interval=10)
    try:
        print('Fortera bridge ready:',mode,flush=True)
        await stop_event.wait()
    finally:
        ws_server.close()
        await ws_server.wait_closed()
        if control_server:
            control_server.close();await control_server.wait_closed()
        monitor.cancel();await asyncio.gather(monitor,return_exceptions=True)
        if video_server: await video_server.close()
        # Graceful local shutdown: request a hold even if the browser session already disconnected.
        if hasattr(backend,'hold'):
            try: await asyncio.wait_for(backend.hold(),3)
            except Exception: pass
        if getattr(backend,'reader',None): backend.reader.stop()
        for task in getattr(backend,'tasks',[]):
            task.cancel()
        if getattr(backend,'tasks',None): await asyncio.gather(*backend.tasks,return_exceptions=True)
        for attr in ('transport','state_transport'):
            transport=getattr(backend,attr,None)
            if transport: transport.close()
if __name__=='__main__':asyncio.run(main())