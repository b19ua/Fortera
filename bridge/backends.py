"""Companion-computer backends, never imported by the hosted web application."""
import asyncio, math, time
from policy import preflight
from obstacles import ObstacleSensing, receive_ranges

class BenchBackend:
    mode = 'bench'
    def __init__(self, count=5):
        self.vehicles = {f'F{i+1:02}': dict(id=f'F{i+1:02}', backend='bench', lat=25.1972, lon=55.2744+i*0.0001, relative_alt_m=0, battery_pct=100, armed=False, ready=True, mode='GROUND', capabilities=['hold','takeoff','goto','return_home','land']) for i in range(count)}
        self.waypoints=[];self.landing_radius_m=12;self.min_clearance_m=10
        for v in self.vehicles.values(): v.update(home_lat=v['lat'],home_lon=v['lon'])
    async def start(self): pass
    async def execute(self, vehicle, action):
        v=self.vehicles[vehicle]; action=preflight(v,action,self.waypoints,self.landing_radius_m,getattr(self,'min_clearance_m',10));kind=action['type']
        if kind=='takeoff': v.update(relative_alt_m=action['altitude_m'], armed=True, mode='HOLD')
        elif kind=='goto':
            point=next(p for p in self.waypoints if p['id']==action['waypoint_id'])
            v.update(lat=point['lat'],lon=point['lon'],relative_alt_m=point['relative_alt_m'],mode='HOLD')
        elif kind in ('land','return_home'):
            if kind=='return_home':v.update(lat=v['home_lat'],lon=v['home_lon'])
            v.update(relative_alt_m=0,armed=False,mode='GROUND')
        elif kind=='hold': v['mode']='HOLD' if v['armed'] else 'GROUND'
    async def hold(self):
        for vid in self.vehicles: await self.execute(vid,{'type':'hold'})

class MavsdkBackend:
    """MAVSDK handles MAVLink framing and controller acknowledgements. Never auto-arms."""
    mode='mavsdk'
    def __init__(self, endpoints):
        try:
            from mavsdk_grpc import System
        except ImportError:
            from mavsdk import System
        self.systems={vid:System(port=50051+i) for i,vid in enumerate(endpoints)}
        self.endpoints=endpoints; self.tasks=[]; self.positions={}; self.health={}; self.homes={}; self.updated={}; self.health_updated={}; self.battery_updated={}; self.armed_updated={}
        self.waypoints=[];self.landing_radius_m=12
        self.min_clearance_m=10;self.obstacles={vid:ObstacleSensing() for vid in endpoints}
        self.vehicles={vid:dict(id=vid,backend='mavsdk',lat=0,lon=0,relative_alt_m=0,battery_pct=0,armed=False,ready=False,mode='CONNECTING',capabilities=['hold','takeoff','goto','return_home','land']) for vid in endpoints}
    async def start(self):
        for vid,system in self.systems.items():
            await system.connect(system_address=self.endpoints[vid])
            async def position(vid=vid,system=system):
                async for p in system.telemetry.position():
                    self.positions[vid]=p; self.updated[vid]=time.monotonic()
                    self.vehicles[vid].update(lat=p.latitude_deg,lon=p.longitude_deg,relative_alt_m=p.relative_altitude_m)
            async def home(vid=vid,system=system):
                async for p in system.telemetry.home():
                    self.homes[vid]=(p.latitude_deg,p.longitude_deg,p.absolute_altitude_m)
                    self.vehicles[vid].update(home_lat=p.latitude_deg,home_lon=p.longitude_deg)
            async def health(vid=vid,system=system):
                async for h in system.telemetry.health(): self.health[vid]=h;self.health_updated[vid]=time.monotonic()
            async def armed(vid=vid,system=system):
                async for a in system.telemetry.armed(): self.vehicles[vid]['armed']=a;self.armed_updated[vid]=time.monotonic()
            async def battery(vid=vid,system=system):
                async for b in system.telemetry.battery(): self.vehicles[vid]['battery_pct']=max(0,min(100,b.remaining_percent*100));self.battery_updated[vid]=time.monotonic()
            async def mode(vid=vid,system=system):
                async for m in system.telemetry.flight_mode(): self.vehicles[vid]['mode']=str(m)
            for fn in (position,home,health,armed,battery,mode): self.tasks.append(asyncio.create_task(fn()))
            for name in ('DISTANCE_SENSOR','OBSTACLE_DISTANCE'):
                self.tasks.append(asyncio.create_task(receive_ranges(system,name,self.obstacles[vid])))
    def refresh(self):
        for vid,v in self.vehicles.items():
            h=self.health.get(vid)
            now=time.monotonic()
            v['ready']=bool(h and h.is_global_position_ok and h.is_home_position_ok and now-self.updated.get(vid,0)<2 and now-self.health_updated.get(vid,0)<5 and now-self.battery_updated.get(vid,0)<5 and now-self.armed_updated.get(vid,0)<5 and vid in self.homes)
            sensing=self.obstacles[vid].snapshot(now)
            v['capabilities']=[c for c in v['capabilities'] if c!='obstacle_sensing']
            if sensing is not None:
                v['obstacle_sensing']=sensing
                if sensing['healthy']:v['capabilities'].append('obstacle_sensing')
    async def execute(self,vid,a):
        self.refresh(); v=self.vehicles[vid]; s=self.systems[vid]
        a=preflight(v,a,self.waypoints,self.landing_radius_m,getattr(self,'min_clearance_m',10))
        # Arming remains exclusively with the local qualified operator.
        if a['type']=='hold':
            # Best-effort hold on link loss must not require fresh GPS.
            if v['armed']: await s.action.hold()
        else:
            if not v['ready']: raise ValueError('Fresh GPS, home, health and battery telemetry required')
            if not v['armed']: raise ValueError('Aircraft must be armed locally by the operator')
            if a['type']=='takeoff': await s.action.set_takeoff_altitude(a['altitude_m']); await s.action.takeoff()
            elif a['type']=='goto':
                point=next(p for p in self.waypoints if p['id']==a['waypoint_id']);home=self.homes[vid]
                await s.action.goto_location(point['lat'],point['lon'],home[2]+point['relative_alt_m'],0)
            elif a['type']=='return_home': await s.action.return_to_launch()
            elif a['type']=='land': await s.action.land()
        # Completed means action API acknowledged, NOT arrival or mission success.
    async def hold(self):
        await asyncio.gather(*(self.execute(vid,{'type':'hold'}) for vid in self.vehicles),return_exceptions=True)

class Ros2Backend:
    """Nav2 action client for a ground robot in the local map (metres), not WGS84."""
    mode='ros2'
    def __init__(self):
        import rclpy
        from rclpy.action import ActionClient
        from nav2_msgs.action import NavigateToPose
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import BatteryState
        from geometry_msgs.msg import Twist
        rclpy.init(); self.rclpy=rclpy; self.node=rclpy.create_node('fortera_bridge');self.goal=None;self.updated=0;self.battery_updated=0
        self.action_type=NavigateToPose; self.client=ActionClient(self.node,NavigateToPose,'navigate_to_pose')
        self.publisher=self.node.create_publisher(Twist,'cmd_vel',10);self.twist=Twist
        self.vehicles={'R01':dict(id='R01',backend='ros2',lat=0,lon=0,relative_alt_m=0,battery_pct=0,armed=False,ready=False,mode='IDLE',local_x_m=0,local_y_m=0,capabilities=['hold','navigate'])}
        self.waypoints=[];self.landing_radius_m=12
        self.node.create_subscription(Odometry,'odom',self.odom,10);self.node.create_subscription(BatteryState,'battery_state',self.battery,10)
    def odom(self,msg):
        self.updated=time.monotonic();self.vehicles['R01'].update(local_x_m=msg.pose.pose.position.x,local_y_m=msg.pose.pose.position.y)
    def battery(self,msg):
        self.battery_updated=time.monotonic();self.vehicles['R01']['battery_pct']=max(0,min(100,msg.percentage*100))
    def refresh(self):
        self.vehicles['R01']['ready']=time.monotonic()-self.updated<2 and time.monotonic()-self.battery_updated<5 and self.client.server_is_ready()
    async def start(self):
        async def spin():
            while True: self.rclpy.spin_once(self.node,timeout_sec=0);self.refresh();await asyncio.sleep(.02)
        self.spin_task=asyncio.create_task(spin())
    async def wait(self,future):
        while not future.done(): await asyncio.sleep(.02)
        return future.result()
    async def execute(self,vid,a):
        if a['type']=='hold':
            if self.goal: await self.wait(self.goal.cancel_goal_async());self.goal=None
            self.publisher.publish(self.twist());self.vehicles[vid]['mode']='HOLD';return
        self.refresh()
        if not self.vehicles[vid]['ready'] or self.vehicles[vid]['battery_pct']<30: raise ValueError('Fresh odometry, battery and active Nav2 required')
        goal=self.action_type.Goal();goal.pose.header.frame_id='map';goal.pose.header.stamp=self.node.get_clock().now().to_msg()
        goal.pose.pose.position.x=a['x_m'];goal.pose.pose.position.y=a['y_m'];goal.pose.pose.orientation.z=math.sin(a['yaw_rad']/2);goal.pose.pose.orientation.w=math.cos(a['yaw_rad']/2)
        self.goal=await self.wait(self.client.send_goal_async(goal))
        if not self.goal.accepted: raise ValueError('Nav2 rejected the goal')
        self.vehicles[vid]['mode']='NAVIGATING'
    async def hold(self): await self.execute('R01',{'type':'hold'})