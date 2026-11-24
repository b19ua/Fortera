import asyncio,json,secrets,time,unittest
from websockets.asyncio.server import serve
from websockets.asyncio.client import connect
from gateway import Gateway,VERSION,validate_command
from backends import BenchBackend, MavsdkBackend
from policy import load_waypoints, preflight
from unittest.mock import AsyncMock

class ValidationTests(unittest.TestCase):
    def test_stop_hover_all_states(self):
        for mode,armed,battery in [('GROUND',False,100),('FLIGHT',True,80),('HOLD',True,20),('HOLD',True,1),('MISSION',True,80),('PENDING',True,80)]:
            for kind in ('stop','hover'):
                with self.subTest(mode=mode,battery=battery,kind=kind):
                    v=dict(mode=mode,armed=armed,battery_pct=battery,ready=False,capabilities=[])
                    self.assertEqual(preflight(v,{'type':kind},[]),{'type':'hold'})
    def test_unsupported_and_unknown_waypoint(self):
        v=BenchBackend().vehicles['F01']
        for kind in ('approach','follow','orbit','inspect','look_at','scan_around','set_altitude','camera_pitch'):
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'Unsupported'):preflight(v,{'type':kind},[])
        with self.assertRaisesRegex(ValueError,'Unknown waypoint_id'):preflight(v,{'type':'goto','waypoint_id':'missing'},[])
        with self.assertRaisesRegex(ValueError,'Unexpected'):preflight(v,dict(type='goto',lat=25.1972,lon=55.2744,relative_alt_m=10,yaw_deg=0),[])
    def test_battery_threshold_and_recovery_actions(self):
        v=BenchBackend().vehicles['F01'];points=load_waypoints('[{"id":"gate","name":"Gate","lat":25.1973,"lon":55.2744,"relative_alt_m":10}]')
        for action in ({'type':'takeoff','altitude_m':10},{'type':'goto','waypoint_id':'gate'}):
            v['battery_pct']=29
            with self.assertRaisesRegex(ValueError,'30%'):preflight(v,action,points)
            v['battery_pct']=30;self.assertEqual(preflight(v,action,points),action)
        v['battery_pct']=0
        for kind in ('hold','return_home','land'):self.assertEqual(preflight(v,{'type':kind},points),{'type':kind})
    def test_home_only_landing_and_custom_radius(self):
        v=BenchBackend().vehicles['F01'];v['lat']+=0.0002
        with self.assertRaisesRegex(ValueError,'return_home'):preflight(v,{'type':'land'},[])
        self.assertEqual(preflight(v,{'type':'land'},[],30),{'type':'land'})
    def test_invalid_plan_is_atomic(self):
        b=BenchBackend();msg=dict(v=VERSION,type='command',id='batch',vehicle='F01',deadline_ms=104000,actions=[{'type':'takeoff','altitude_m':10},{'type':'orbit'}])
        with self.assertRaises(ValueError):validate_command(msg,b.vehicles,100000)
        self.assertEqual(b.vehicles['F01']['relative_alt_m'],0)
    def test_goto_then_land_outside_home_rejects_whole_plan(self):
        b=BenchBackend();points=[dict(id='away',name='Away',lat=25.1974,lon=55.2744,relative_alt_m=10)]
        msg=dict(v=VERSION,type='command',id='batch',vehicle='F01',deadline_ms=104000,actions=[{'type':'goto','waypoint_id':'away'},{'type':'land'}])
        with self.assertRaisesRegex(ValueError,'return_home'):validate_command(msg,b.vehicles,100000,points)
        self.assertEqual(b.vehicles['F01']['lat'],25.1972)
    def test_waypoint_configuration_rejects_duplicates_and_invalid_coordinates(self):
        point=dict(id='gate',name='Gate',lat=25.1973,lon=55.2744,relative_alt_m=10)
        self.assertEqual(load_waypoints(json.dumps([point])),[point])
        for points in ([point,point],[dict(point,lat=100)],[dict(point,relative_alt_m=31)]):
            with self.assertRaises(ValueError):load_waypoints(json.dumps(points))
    def test_bounds_and_deadlines(self):
        b=BenchBackend();now=100000
        def msg(alt):return dict(v=VERSION,type='command',id='a',vehicle='F01',deadline_ms=now+3000,action=dict(type='takeoff',altitude_m=alt))
        self.assertEqual(validate_command(msg(30),b.vehicles,now)[1]['altitude_m'],30)
        for alt in (2,31,float('nan'),True):
            with self.assertRaises(ValueError):validate_command(msg(alt),b.vehicles,now)
        m=msg(10);m['deadline_ms']=now-1
        with self.assertRaises(ValueError):validate_command(m,b.vehicles,now)
        m=msg(10);m['action']['motor']=1
        with self.assertRaises(ValueError):validate_command(m,b.vehicles,now)

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token=secrets.token_urlsafe(32);self.backend=BenchBackend();self.g=Gateway(self.backend,self.token,{'http://localhost:8080'})
        self.server=await serve(self.g.handler,'127.0.0.1',0)
        self.url=f'ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}'
    async def asyncTearDown(self):self.server.close();await self.server.wait_closed()
    async def open(self,token=None,origin='http://localhost:8080'):
        ws=await connect(self.url,origin=origin);await ws.send(json.dumps(dict(v=VERSION,type='hello',token=token or self.token)));return ws
    async def receive(self,ws,kind,cid=None):
        while True:
            msg=json.loads(await asyncio.wait_for(ws.recv(),2))
            if msg['type']==kind and (not cid or msg.get('id')==cid):return msg
    async def test_pairing_telemetry_command_readback_and_duplicate(self):
        async with await self.open() as ws:
            self.assertEqual((await self.receive(ws,'welcome'))['mode'],'bench')
            self.assertEqual(len((await self.receive(ws,'telemetry'))['vehicles']),5)
            m=dict(v=VERSION,type='command',id='flight',vehicle='F01',deadline_ms=time.time()*1000+4000,action=dict(type='takeoff',altitude_m=10))
            await ws.send(json.dumps(m));self.assertEqual((await self.receive(ws,'ack','flight'))['status'],'accepted');self.assertEqual((await self.receive(ws,'ack','flight'))['status'],'completed')
            state=await self.receive(ws,'telemetry');self.assertEqual(state['vehicles'][0]['relative_alt_m'],10)
            self.backend.vehicles['F01']['relative_alt_m']=12
            await ws.send(json.dumps(m));self.assertEqual((await self.receive(ws,'ack','flight'))['status'],'completed');self.assertEqual(self.backend.vehicles['F01']['relative_alt_m'],12)
            m['action']['altitude_m']=20;await ws.send(json.dumps(m));self.assertEqual((await self.receive(ws,'ack','flight'))['status'],'rejected')
        await asyncio.sleep(.05);self.assertEqual(self.backend.vehicles['F01']['mode'],'HOLD')
    async def test_wrong_token_origin_and_busy_lease(self):
        from websockets.exceptions import ConnectionClosed
        for token,origin in ((secrets.token_urlsafe(32),'http://localhost:8080'),(self.token,'https://evil.invalid')):
            with self.assertRaises(ConnectionClosed):
                ws=await self.open(token,origin)
                await ws.recv()
        async with await self.open() as first:
            await self.receive(first,'welcome')
            ws=await self.open()
            with self.assertRaises(ConnectionClosed):await ws.recv()
            await ws.close()
    async def test_physical_backend_disabled_by_default(self):
        self.backend.mode='mavsdk'
        async with await self.open() as ws:
            await self.receive(ws,'welcome')
            await ws.send(json.dumps(dict(v=VERSION,type='command',id='blocked',vehicle='F01',deadline_ms=time.time()*1000+4000,action={'type':'takeoff','altitude_m':10})))
            self.assertEqual((await self.receive(ws,'ack','blocked'))['status'],'rejected')
            self.assertEqual(self.backend.vehicles['F01']['relative_alt_m'],0)
    async def test_waypoints_welcome_telemetry_goto_and_land_rejection(self):
        self.g.waypoints[:]=load_waypoints('[{"id":"gate","name":"Gate","lat":25.1974,"lon":55.2744,"relative_alt_m":10}]')
        async with await self.open() as ws:
            welcome=await self.receive(ws,'welcome');self.assertEqual(welcome['waypoints'][0]['id'],'gate')
            self.assertEqual((await self.receive(ws,'telemetry'))['waypoints'],welcome['waypoints'])
            for cid,action,status in [('go',{'type':'goto','waypoint_id':'gate'},'completed'),('land',{'type':'land'},'rejected'),('bad',{'type':'goto','waypoint_id':'unknown'},'rejected')]:
                await ws.send(json.dumps(dict(v=VERSION,type='command',id=cid,vehicle='F01',deadline_ms=time.time()*1000+4000,action=action)))
                ack=await self.receive(ws,'ack',cid)
                if ack['status']=='accepted':ack=await self.receive(ws,'ack',cid)
                self.assertEqual(ack['status'],status)
                if cid=='land':self.assertIn('return_home',ack['reason'])
            self.assertEqual(self.backend.vehicles['F01']['lat'],25.1974)
    async def test_batch_with_error_does_not_execute_first_action(self):
        async with await self.open() as ws:
            await self.receive(ws,'welcome')
            await ws.send(json.dumps(dict(v=VERSION,type='command',id='atomic',vehicle='F01',deadline_ms=time.time()*1000+4000,actions=[{'type':'takeoff','altitude_m':10},{'type':'orbit'}])))
            self.assertEqual((await self.receive(ws,'ack','atomic'))['status'],'rejected')
            self.assertEqual(self.backend.vehicles['F01']['relative_alt_m'],0)
    async def test_hold_aliases_preempt_pending_command_at_zero_battery(self):
        original=self.backend.execute
        started=asyncio.Event()
        async def delayed(vid,a):
            if a['type']=='takeoff':started.set();await asyncio.sleep(30)
            else:await original(vid,a)
        self.backend.execute=delayed
        async with await self.open() as ws:
            await self.receive(ws,'welcome')
            async def send(cid,a):await ws.send(json.dumps(dict(v=VERSION,type='command',id=cid,vehicle='F01',deadline_ms=time.time()*1000+4000,action=a)))
            await send('slow',{'type':'takeoff','altitude_m':10});await self.receive(ws,'ack','slow');await started.wait()
            self.backend.vehicles['F01']['battery_pct']=0
            await send('stop',{'type':'stop'})
            await self.receive(ws,'ack','stop');self.assertEqual((await self.receive(ws,'ack','stop'))['status'],'completed')
            await send('hover',{'type':'hover'})
            await self.receive(ws,'ack','hover');self.assertEqual((await self.receive(ws,'ack','hover'))['status'],'completed')
    async def test_old_protocol_fails_closed(self):
        from websockets.exceptions import ConnectionClosed
        ws=await connect(self.url,origin='http://localhost:8080')
        await ws.send(json.dumps(dict(v='fortera.bridge/1',type='hello',token=self.token)))
        with self.assertRaises(ConnectionClosed):await ws.recv()
        await ws.close()

class BackendSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_bench_battery_and_landing_checks_before_mutation(self):
        b=BenchBackend();v=b.vehicles['F01'];v['battery_pct']=29
        with self.assertRaisesRegex(ValueError,'30%'):await b.execute('F01',{'type':'takeoff','altitude_m':10})
        self.assertEqual(v['relative_alt_m'],0)
        v['lat']+=.0002
        with self.assertRaisesRegex(ValueError,'return_home'):await b.execute('F01',{'type':'land'})
        v['battery_pct']=0;await b.execute('F01',{'type':'return_home'});await b.execute('F01',{'type':'land'})
        self.assertEqual(v['lat'],v['home_lat'])
    async def test_mavsdk_rules_with_mock_controller_no_hardware(self):
        b=object.__new__(MavsdkBackend);b.waypoints=load_waypoints('[{"id":"gate","name":"Gate","lat":25.1973,"lon":55.2744,"relative_alt_m":10}]');b.landing_radius_m=12
        v=dict(BenchBackend().vehicles['F01'],backend='mavsdk',armed=True)
        b.vehicles={'F01':v};b.homes={'F01':(v['lat'],v['lon'],50)};b.refresh=lambda:None
        action=AsyncMock();b.systems={'F01':type('System',(),{'action':action})()}
        for battery in (0,29):
            v['battery_pct']=battery
            for command in ({'type':'takeoff','altitude_m':10},{'type':'goto','waypoint_id':'gate'}):
                with self.assertRaisesRegex(ValueError,'30%'):await b.execute('F01',command)
        v['battery_pct']=30;await b.execute('F01',{'type':'takeoff','altitude_m':10});await b.execute('F01',{'type':'goto','waypoint_id':'gate'})
        action.goto_location.assert_awaited_once_with(25.1973,55.2744,60,0)
        v['lat']+=.0002
        with self.assertRaisesRegex(ValueError,'return_home'):await b.execute('F01',{'type':'land'})
        action.land.assert_not_awaited()
        v['lat']=v['home_lat'];v['battery_pct']=0
        for kind in ('hold','return_home','land'):await b.execute('F01',{'type':kind})
        action.land.assert_awaited_once();action.return_to_launch.assert_awaited_once()
if __name__=='__main__':unittest.main()