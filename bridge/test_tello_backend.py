"""Automated tests against the fake Tello SDK. These are NOT flight tests."""
import asyncio, json, math, secrets, socket, time, unittest
import numpy as np
from fake_tello import FakeTello
from tello_backend import TelloBackend, parse_state
from perception import PerceptionMonitor
from policy import resolve_profile

def free_port():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close();return p
TEX=(np.random.default_rng(2).random((240,320))*255).astype(np.uint8)

class TelloBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self,**fake_kw):
        self.cmd,self.state=free_port(),free_port()
        self.fake=FakeTello(cmd_port=self.cmd,state_port=self.state,**fake_kw);await self.fake.start()
        profile=resolve_profile('indoor_demo');profile['limits']['settle_ms']=0  # test-only: no settle wait between steps
        self.b=TelloBackend(host='127.0.0.1',cmd_port=self.cmd,state_port=self.state,video=False,profile=profile,monitor=PerceptionMonitor(),land_after_s=0.3,mission_pads=fake_kw.get('edu',False))
        self.video=True;self.feeder=asyncio.create_task(self.feed());await self.b.start()
        await self.wait(lambda:self.b.state_at is not None)
    async def asyncTearDown(self):
        self.feeder.cancel()
        for t in self.b.tasks: t.cancel()
        self.b.transport.close();self.b.state_transport.close();self.fake.close()
    async def feed(self):
        i=0
        while True:
            if self.video: self.b.monitor.push_frame(np.roll(TEX,i%3,axis=1));i+=1
            await asyncio.sleep(0.05)
    async def wait(self,cond,timeout=3):
        end=time.monotonic()+timeout
        while not cond():
            if time.monotonic()>end: raise AssertionError('condition not met')
            await asyncio.sleep(0.02)
    def v(self): self.b.refresh();return self.b.vehicles['T01']
    async def fly(self):
        await self.b.execute('T01',{'type':'takeoff','altitude_m':1});await self.wait(lambda:self.b.state.get('h',0)>=30)
        self.assertTrue(self.v()['estimate']['valid'])

    async def test_state_parsing_and_telemetry_fields(self):
        self.assertEqual(parse_state('mid:-1;h:80;bat:87;baro:1.25;mpry:0,0,0;\r\n'),{'mid':-1,'h':80,'bat':87,'baro':1.25})
        v=self.v();self.assertEqual(v['backend'],'tello');self.assertEqual(v['battery_pct'],90);self.assertTrue(v['perception']['warning_only'])
        self.assertIn(v['sdk_state']['vps_hint'],('likely_ok','degraded','unknown'));self.assertFalse(v['estimate']['valid'])
        self.assertNotIn('local_x_m',v)
    async def test_takeoff_step_updates_estimate_and_sdk_commands(self):
        await self.fly()
        await self.b.execute('T01',{'type':'navigate','x_m':0.3,'y_m':0,'yaw_rad':0})
        v=self.v();self.assertAlmostEqual(v['estimate']['x_m'],0.3,2);self.assertGreater(v['estimate']['uncertainty_m'],0.1);self.assertAlmostEqual(v['local_x_m'],0.3,2)
        self.assertIn('takeoff',self.fake.log);self.assertIn('forward 30',self.fake.log);self.assertNotIn('up',' '.join(self.fake.log).split())
        self.assertIn('speed 20',self.fake.log)
    async def test_stop_preempts_running_step_and_invalidates_estimate(self):
        await self.fly();self.fake.scale=0.2  # forward 50 → 0.5 s
        task=asyncio.create_task(self.b.execute('T01',{'type':'navigate','x_m':0.5,'y_m':0,'yaw_rad':0}))
        await asyncio.sleep(0.15);task.cancel()   # what the gateway does on hold
        with self.assertRaises(asyncio.CancelledError): await task
        t=time.monotonic();await self.b.execute('T01',{'type':'hold'});self.assertLess(time.monotonic()-t,0.5)
        await asyncio.sleep(0.05)
        self.assertIn('stop',self.fake.log);self.assertIn('rc 0 0 0 0',self.fake.log);self.assertAlmostEqual(self.fake.x,0.0)
        v=self.v();self.assertFalse(v['estimate']['valid']);self.assertEqual(v['estimate']['invalid_reason'],'interrupted');self.assertNotIn('local_x_m',v)
        for a in ({'type':'navigate','x_m':0.3,'y_m':0,'yaw_rad':0},{'type':'return_home'}):
            with self.assertRaisesRegex(ValueError,'invalid'): await self.b.execute('T01',a)
        await self.b.execute('T01',{'type':'land'})
    async def test_stale_video_blocks_moves_and_watchdog_holds_then_lands(self):
        await self.fly();self.video=False;await asyncio.sleep(0.6)
        with self.assertRaisesRegex(ValueError,'Camera frame'): await self.b.execute('T01',{'type':'navigate','x_m':0.3,'y_m':0,'yaw_rad':0})
        self.b.refresh();self.assertIn('video',await self.b.watchdog(True))
        self.assertIn('stop',self.fake.log);self.assertFalse(self.v()['estimate']['valid'])
        await asyncio.sleep(0.35);self.b.refresh();await self.b.watchdog(True);await asyncio.sleep(0.05)
        self.assertIn('land',self.fake.log)
    async def test_watchdog_reports_but_does_not_act_when_hardware_disabled(self):
        await self.fly();self.video=False;await asyncio.sleep(0.6);n=self.fake.log.count('stop')
        self.assertIsNotNone(await self.b.watchdog(False));self.assertEqual(self.fake.log.count('stop'),n)
    async def test_telemetry_loss(self):
        await self.fly();self.fake.send_state=False;await asyncio.sleep(1.2)
        v=self.v();self.assertFalse(v['ready']);self.assertEqual(v['estimate']['invalid_reason'],'telemetry_gap')
        self.assertEqual(self.b.hold_reason(),'telemetry_lost')
    async def test_low_battery_blocks_takeoff(self):
        self.fake.battery=45;await asyncio.sleep(0.25)
        with self.assertRaisesRegex(ValueError,'50%'): await self.b.execute('T01',{'type':'takeoff','altitude_m':1})
        self.assertNotIn('takeoff',self.fake.log)
    async def test_sdk_error_invalidates_estimate(self):
        await self.fly();self.fake.fail_next='forward'
        with self.assertRaisesRegex(ValueError,'error'): await self.b.execute('T01',{'type':'navigate','x_m':0.3,'y_m':0,'yaw_rad':0})
        self.assertEqual(self.v()['estimate']['invalid_reason'],'sdk_error')
    async def test_uncommanded_motion_invalidates_estimate(self):
        await self.fly();await asyncio.sleep(1.6);self.fake.drift_vgx=5;await asyncio.sleep(0.25)
        self.assertEqual(self.v()['estimate']['invalid_reason'],'unconfirmed_action')
    async def test_reconnect_invalidates_estimate(self):
        await self.fly();self.b.on_session_start();self.assertEqual(self.v()['estimate']['invalid_reason'],'reconnect')
    async def test_per_action_timeouts_cover_slow_steps(self):
        self.assertGreater(self.b.timeout_for({'type':'navigate','x_m':0.5,'y_m':0,'yaw_rad':0}),10)
        self.assertGreater(self.b.timeout_for({'type':'takeoff','altitude_m':1}),10)
    async def test_return_home_flies_estimated_vector(self):
        await self.fly()
        await self.b.execute('T01',{'type':'navigate','x_m':0.4,'y_m':0,'yaw_rad':0})
        await self.b.execute('T01',{'type':'return_home'})
        self.assertIn('back 40',self.fake.log);self.assertAlmostEqual(self.fake.x,0,2)
        v=self.v();self.assertTrue(v['estimate']['valid']);self.assertEqual(v['estimate']['x_m'],0)
    async def test_standard_tello_has_no_mission_pads(self):
        self.assertNotIn('mission_pad',self.v()['capabilities'])
        await self.fly()
        with self.assertRaisesRegex(ValueError,'Tello EDU'): await self.b.execute('T01',{'type':'pad_step','mid':1,'x_cm':0,'y_cm':0,'z_cm':80})

class StopMissingTests(TelloBackendTests):
    async def asyncSetUp(self): await TelloBackendTests.asyncSetUp(self,supports_stop=False)
    async def test_hold_still_sends_rc_when_stop_unsupported(self):
        await self.fly();await self.b.execute('T01',{'type':'hold'});await asyncio.sleep(0.05)
        self.assertIn('rc 0 0 0 0',self.fake.log)

class EduPadTests(TelloBackendTests):
    async def asyncSetUp(self): await TelloBackendTests.asyncSetUp(self,edu=True,pad=1)
    async def test_standard_tello_has_no_mission_pads(self): pass
    async def test_mission_pad_detection_and_step(self):
        v=self.v();self.assertIn('mission_pad',v['capabilities']);self.assertEqual(v['sdk_state']['model'],'tello_edu');self.assertEqual(v['mission_pad']['mid'],1)
        self.assertIn('mon',self.fake.log);self.assertIn('mdirection 0',self.fake.log)
        await self.fly();await self.b.execute('T01',{'type':'pad_step','mid':1,'x_cm':0,'y_cm':0,'z_cm':80})
        self.assertIn('go 0 0 80 20 m1',self.fake.log)
        with self.assertRaisesRegex(ValueError,'not detected'): await self.b.execute('T01',{'type':'pad_step','mid':3,'x_cm':0,'y_cm':0,'z_cm':80})

class GatewayTelloTests(unittest.IsolatedAsyncioTestCase):
    """Browser protocol over WebSocket → gateway → Tello backend → fake SDK."""
    async def test_welcome_profile_telemetry_fields_and_takeoff_ack(self):
        from websockets.asyncio.server import serve
        from websockets.asyncio.client import connect
        from gateway import Gateway
        cmd,state=free_port(),free_port();fake=FakeTello(cmd_port=cmd,state_port=state);await fake.start()
        b=TelloBackend(host='127.0.0.1',cmd_port=cmd,state_port=state,video=False,monitor=PerceptionMonitor());await b.start()
        stop=asyncio.Event()
        async def feed():
            i=0
            while not stop.is_set(): b.monitor.push_frame(np.roll(TEX,i%3,axis=1));i+=1;await asyncio.sleep(0.05)
        f=asyncio.create_task(feed())
        token=secrets.token_urlsafe(32);g=Gateway(b,token,{'http://localhost:8080'},enabled=True)
        server=await serve(g.handler,'127.0.0.1',0);url=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}'
        try:
            async with connect(url,origin='http://localhost:8080') as ws:
                await ws.send(json.dumps(dict(v='fortera.bridge/2',type='hello',token=token)))
                async def recv(kind,cid=None):
                    while True:
                        m=json.loads(await asyncio.wait_for(ws.recv(),5))
                        if m['type']==kind and (cid is None or m.get('id')==cid): return m
                w=await recv('welcome');self.assertEqual(w['mode'],'tello');self.assertEqual(w['profile']['id'],'indoor_demo');self.assertEqual(w['profile']['limits']['step_max_m'],0.5)
                await asyncio.sleep(0.2);t=await recv('telemetry');veh=t['vehicles'][0]
                for k in ('perception','sdk_state','estimate'): self.assertIn(k,veh)
                await ws.send(json.dumps(dict(v='fortera.bridge/2',type='command',id='to',vehicle='T01',deadline_ms=time.time()*1000+4000,action={'type':'takeoff','altitude_m':2})))
                self.assertEqual((await recv('ack','to'))['status'],'rejected')
                await ws.send(json.dumps(dict(v='fortera.bridge/2',type='command',id='to1',vehicle='T01',deadline_ms=time.time()*1000+4000,action={'type':'takeoff','altitude_m':1})))
                self.assertEqual((await recv('ack','to1'))['status'],'accepted');self.assertEqual((await recv('ack','to1'))['status'],'completed')
        finally:
            stop.set();f.cancel();server.close();await server.wait_closed()
            for task in b.tasks: task.cancel()
            b.transport.close();b.state_transport.close();fake.close()

if __name__=='__main__': unittest.main()