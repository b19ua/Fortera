"""Fortera reference bridge. External companion process; bench mode by default."""
import asyncio, collections, hmac, json, math, os, secrets, ssl, time
from websockets.asyncio.server import serve
from backends import BenchBackend, MavsdkBackend, Ros2Backend
from policy import preflight_plan, load_waypoints, landing_radius
from obstacles import sensing_reason
VERSION='fortera.bridge/2'


def validate_command(msg,vehicles,now=None,waypoints=None,radius=12,minimum=10):
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
    actions=preflight_plan(v,raw,waypoints or [],radius,minimum)
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
        async def send(msg): await ws.send(json.dumps(dict(v=VERSION,**msg),allow_nan=False))
        await send(dict(type='welcome',session=secrets.token_hex(16),mode=self.backend.mode,waypoints=self.waypoints,landing_radius_m=self.landing_radius_m,min_clearance_m=self.min_clearance_m))
        async def telemetry():
            nonlocal seq
            while True:
                if time.monotonic()-last_ping>5: await ws.close(1008,'Heartbeat lost');return
                if hasattr(self.backend,'refresh'): self.backend.refresh()
                await self.enforce_obstacle_safety()
                await send(dict(type='telemetry',seq=seq,vehicles=list(self.backend.vehicles.values()),waypoints=self.waypoints,landing_radius_m=self.landing_radius_m,min_clearance_m=self.min_clearance_m));seq+=1;await asyncio.sleep(.2)
        ticker=asyncio.create_task(telemetry())
        async def execute(msg,vid,a):
            cid=msg['id']
            try:
                for action in (a if isinstance(a,list) else [a]):
                    await asyncio.wait_for(self.backend.execute(vid,action),10)
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
                    vid,a=validate_command(msg,self.backend.vehicles,waypoints=self.waypoints,radius=self.landing_radius_m,minimum=self.min_clearance_m)
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

async def main():
    token=os.environ.get('FORTERA_BRIDGE_TOKEN','');origins=set(os.environ.get('FORTERA_ALLOWED_ORIGINS','http://localhost:8080').split(','))
    if len(token)<24: raise SystemExit('Set a private FORTERA_BRIDGE_TOKEN of at least 24 characters')
    mode=os.environ.get('FORTERA_BACKEND','bench');count=int(os.environ.get('FORTERA_BENCH_COUNT','5'))
    if not 1<=count<=20: raise SystemExit('Bench fleet must contain 1–20 vehicles')
    backend=BenchBackend(count) if mode=='bench' else MavsdkBackend(json.loads(os.environ.get('FORTERA_MAVSDK_ENDPOINTS','{"F01":"udpin://0.0.0.0:14540"}'))) if mode=='mavsdk' else Ros2Backend() if mode=='ros2' else None
    if backend is None: raise SystemExit('Unknown backend')
    if not 1<=len(backend.vehicles)<=20: raise SystemExit('Fleet must contain 1–20 vehicles')
    tls=None
    if os.environ.get('FORTERA_TLS_CERT'):
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(os.environ['FORTERA_TLS_CERT'],os.environ['FORTERA_TLS_KEY'])
    host=os.environ.get('FORTERA_BIND','127.0.0.1')
    if host not in ('127.0.0.1','::1','localhost') and tls is None: raise SystemExit('Non-loopback bridges require TLS')
    points=load_waypoints(os.environ.get('FORTERA_WAYPOINTS','[]'));radius=landing_radius(os.environ.get('FORTERA_LANDING_RADIUS_M','12'))
    minimum=landing_radius(os.environ.get('FORTERA_MIN_CLEARANCE_M','10'))
    await backend.start();gateway=Gateway(backend,token,origins,os.environ.get('FORTERA_ENABLE_HARDWARE')=='1',points,radius,minimum)
    monitor=asyncio.create_task(gateway.monitor_obstacles())
    try:
        async with serve(gateway.handler,host,int(os.environ.get('FORTERA_PORT','8765')),ssl=tls,max_size=8192,ping_interval=10):
            print('Fortera bridge ready:',mode,flush=True);await asyncio.Future()
    finally:
        monitor.cancel();await asyncio.gather(monitor,return_exceptions=True)
if __name__=='__main__':asyncio.run(main())