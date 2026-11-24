import asyncio, json, unittest
from unittest.mock import AsyncMock
from backends import BenchBackend
from gateway import Gateway
from obstacles import ObstacleSensing, receive_ranges
from policy import preflight
from websockets.asyncio.server import serve
from websockets.asyncio.client import connect
from gateway import VERSION

def distance(value=900,orientation=0,sensor=1):
    return dict(id=sensor,orientation=orientation,min_distance=0,max_distance=4000,current_distance=value,signal_quality=100)

class SensorTests(unittest.TestCase):
    def test_distance_sensor_centimetres_orientation_and_one_second_expiry(self):
        cache=ObstacleSensing();self.assertIsNone(cache.snapshot(0))
        cache.ingest('DISTANCE_SENSOR',distance(),now=10)
        state=cache.snapshot(10.999);self.assertTrue(state['healthy']);self.assertEqual(state['nearest_m'],9);self.assertEqual(state['sectors'][0]['sector'],'forward')
        self.assertFalse(cache.snapshot(11)['healthy'])
    def test_obstacle_distance_body_angles_unknown_and_contact(self):
        cache=ObstacleSensing();values=[65535]*72;values[0]=0;values[1]=1000
        cache.ingest('OBSTACLE_DISTANCE',dict(distances=values,increment=45,increment_f=0,angle_offset=0,frame=12,min_distance=20,max_distance=4000),now=10)
        state=cache.snapshot(10);self.assertTrue(state['healthy']);self.assertEqual(state['nearest_m'],0)
        self.assertEqual([s['sector'] for s in state['sectors']],['forward','forward_right'])
    def test_global_frame_is_not_falsely_reported_as_body_clearance(self):
        cache=ObstacleSensing();values=[65535]*72;values[0]=2000
        cache.ingest('OBSTACLE_DISTANCE',dict(distances=values,increment=5,frame=0,min_distance=0,max_distance=4000),now=10)
        self.assertFalse(cache.snapshot(10)['healthy'])
    def test_fresh_source_cannot_mask_a_stale_other_sector(self):
        cache=ObstacleSensing();cache.ingest('DISTANCE_SENSOR',distance(2000,0,1),now=10)
        cache.ingest('DISTANCE_SENSOR',distance(3000,2,2),now=11)
        self.assertFalse(cache.snapshot(11)['healthy'])
    def test_invalid_signal_does_not_refresh_old_clear_measurement(self):
        cache=ObstacleSensing();cache.ingest('DISTANCE_SENSOR',distance(2000),now=10)
        cache.ingest('DISTANCE_SENSOR',dict(distance(2000),signal_quality=1),now=10.2)
        self.assertFalse(cache.snapshot(10.2)['healthy']);self.assertIsNone(cache.snapshot(10.2)['nearest_m'])
    def test_motion_blocks_below_10_and_accepts_boundary(self):
        v=BenchBackend().vehicles['F01'];points=[dict(id='gate',name='Gate',lat=v['lat'],lon=v['lon'],relative_alt_m=10)]
        v['obstacle_sensing']=dict(healthy=True,updated_ms_ago=0,nearest_m=9,sectors=[])
        for a in ({'type':'goto','waypoint_id':'gate'},{'type':'takeoff','altitude_m':10}):
            with self.assertRaisesRegex(ValueError,'obstacle_too_close'):preflight(v,a,points)
        v['obstacle_sensing']['nearest_m']=10
        self.assertEqual(preflight(v,{'type':'goto','waypoint_id':'gate'},points)['type'],'goto')
        with self.assertRaisesRegex(ValueError,'obstacle_too_close'):preflight(v,{'type':'goto','waypoint_id':'gate'},points,minimum=12)
    def test_stale_motion_rejected_but_hold_always_available(self):
        v=BenchBackend().vehicles['F01'];v['obstacle_sensing']=dict(healthy=False,updated_ms_ago=1000,nearest_m=None,sectors=[])
        with self.assertRaisesRegex(ValueError,'obstacle_data_stale'):preflight(v,{'type':'takeoff','altitude_m':10},[])
        v.update(ready=False,battery_pct=0,capabilities=[])
        for a in ('hold','hover','stop'):self.assertEqual(preflight(v,{'type':a},[]),{'type':'hold'})
    def test_orbit_stays_disabled_with_exact_reason(self):
        with self.assertRaisesRegex(ValueError,'requires verified obstacle avoidance; not enabled'):preflight(BenchBackend().vehicles['F01'],{'type':'orbit'},[])

class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_socket_telemetry_reads_back_stale_hold_and_motion_refusal(self):
        backend=BenchBackend(1);v=backend.vehicles['F01'];v.update(armed=True,relative_alt_m=10,mode='TRANSIT',obstacle_sensing=dict(healthy=False,updated_ms_ago=1001,nearest_m=None,sectors=[]))
        token='test-only-obstacle-pairing-value';gateway=Gateway(backend,token,{'http://localhost:8080'})
        async with serve(gateway.handler,'127.0.0.1',0) as server:
            url=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}'
            async with connect(url,origin='http://localhost:8080') as ws:
                await ws.send(json.dumps(dict(v=VERSION,type='hello',token=token)))
                await ws.recv()
                state=json.loads(await asyncio.wait_for(ws.recv(),2))
                self.assertEqual(state['vehicles'][0]['mode'],'HOLD');self.assertEqual(state['vehicles'][0]['safety_reason'],'obstacle_data_stale')
                self.assertEqual(state['min_clearance_m'],10)
                import time
                await ws.send(json.dumps(dict(v=VERSION,type='command',id='blocked',vehicle='F01',deadline_ms=time.time()*1000+4000,action={'type':'takeoff','altitude_m':10})))
                while True:
                    message=json.loads(await asyncio.wait_for(ws.recv(),2))
                    if message['type']=='ack':break
                self.assertEqual(message['status'],'rejected');self.assertEqual(message['reason'],'obstacle_data_stale')
    async def test_stale_data_holds_in_flight_without_browser_and_drops_capability(self):
        backend=BenchBackend(1);v=backend.vehicles['F01'];v.update(armed=True,relative_alt_m=10,mode='TRANSIT',capabilities=['hold','obstacle_sensing'],obstacle_sensing=dict(healthy=False,updated_ms_ago=1000,nearest_m=None,sectors=[]))
        gateway=Gateway(backend,'not-a-real-pairing-token',set())
        original=backend.execute;backend.execute=AsyncMock(side_effect=original)
        await gateway.enforce_obstacle_safety();await gateway.enforce_obstacle_safety()
        backend.execute.assert_awaited_once_with('F01',{'type':'hold'})
        self.assertEqual(v['mode'],'HOLD');self.assertEqual(v['safety_reason'],'obstacle_data_stale');self.assertNotIn('obstacle_sensing',v['capabilities'])
    async def test_disabled_hardware_remains_disabled_even_on_stale_data(self):
        backend=BenchBackend(1);backend.mode='mavsdk';v=backend.vehicles['F01'];v.update(armed=True,relative_alt_m=10,obstacle_sensing=dict(healthy=False,updated_ms_ago=1000,nearest_m=None,sectors=[]))
        backend.execute=AsyncMock();gateway=Gateway(backend,'not-a-real-pairing-token',set())
        await gateway.enforce_obstacle_safety();backend.execute.assert_not_awaited();self.assertFalse(gateway.enabled)
    async def test_stale_watchdog_preempts_pending_motion(self):
        backend=BenchBackend(1);v=backend.vehicles['F01'];v.update(armed=True,relative_alt_m=10,obstacle_sensing=dict(healthy=False,updated_ms_ago=1200,nearest_m=None,sectors=[]))
        gateway=Gateway(backend,'not-a-real-pairing-token',set())
        pending=asyncio.create_task(asyncio.sleep(30));gateway.running['F01']=pending
        await gateway.enforce_obstacle_safety();self.assertTrue(pending.cancelled());self.assertEqual(v['mode'],'HOLD')
    async def test_receive_only_mavsdk_plugin_reads_both_messages(self):
        cache=ObstacleSensing();seen=[]
        class Direct:
            async def message(self,name):
                seen.append(name)
                fields=distance(1200) if name=='DISTANCE_SENSOR' else dict(distances=[1500]+[65535]*71,increment=5,frame=12,min_distance=0,max_distance=4000)
                yield type('Message',(),dict(fields_json=json.dumps(fields),system_id=1,component_id=1))()
        system=type('System',(),{'mavlink_direct':Direct()})()
        await receive_ranges(system,'DISTANCE_SENSOR',cache);await receive_ranges(system,'OBSTACLE_DISTANCE',cache)
        self.assertEqual(seen,['DISTANCE_SENSOR','OBSTACLE_DISTANCE']);self.assertEqual(cache.snapshot()['nearest_m'],12)

if __name__=='__main__':unittest.main()