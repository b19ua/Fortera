"""Pure reference preflight, shared by gateway and companion backends."""
import json, math
from obstacles import sensing_reason

def load_waypoints(raw):
    points=json.loads(raw)
    if not isinstance(points,list) or len(points)>100: raise ValueError('FORTERA_WAYPOINTS must be a list of at most 100 points')
    ids=set()
    for p in points:
        if not isinstance(p,dict) or set(p)!={'id','name','lat','lon','relative_alt_m'}: raise ValueError('Invalid waypoint fields')
        if not isinstance(p['id'],str) or not 1<=len(p['id'])<=64 or p['id'] in ids: raise ValueError('Invalid or duplicate waypoint id')
        if not isinstance(p['name'],str) or not 1<=len(p['name'])<=128: raise ValueError('Invalid waypoint name')
        ids.add(p['id'])
        for field,lo,hi in [('lat',-90,90),('lon',-180,180),('relative_alt_m',3,30)]:
            x=p[field]
            if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not lo<=x<=hi: raise ValueError('Invalid waypoint coordinates')
    return points

def landing_radius(raw):
    value=float(raw)
    if not math.isfinite(value) or value<=0: raise ValueError('Landing radius must be finite and positive')
    return value

def distance(lat,lon,home_lat,home_lon):
    a=math.sin(math.radians(lat-home_lat)/2)**2+math.cos(math.radians(lat))*math.cos(math.radians(home_lat))*math.sin(math.radians(lon-home_lon)/2)**2
    return 6371000*2*math.atan2(math.sqrt(a),math.sqrt(max(0,1-a)))

def preflight(v,a,waypoints,radius=12,minimum=10):
    if not isinstance(a,dict): raise ValueError('Invalid action')
    kind=a.get('type')
    if kind in ('stop','hover'): return {'type':'hold'}
    if kind=='hold': return {'type':'hold'}
    if kind in ('orbit','follow','approach','inspect'):raise ValueError('Unsupported action: requires verified obstacle avoidance; not enabled')
    if kind not in v['capabilities']: raise ValueError('Unsupported action')
    fields={'land':{},'return_home':{},'takeoff':{'altitude_m':(3,30)},'goto':{'waypoint_id':None},'navigate':{'x_m':(-100,100),'y_m':(-100,100),'yaw_rad':(-math.pi,math.pi)}}
    if kind not in fields: raise ValueError('Unsupported action')
    if v['backend']=='mavsdk' and (not v['ready'] or not v['armed']): raise ValueError('Fresh telemetry and locally armed aircraft required')
    if set(a)!={'type',*fields[kind]}: raise ValueError('Unexpected action fields')
    for key,bounds in fields[kind].items():
        value=a[key]
        if bounds is None:
            if not isinstance(value,str) or not 1<=len(value)<=64: raise ValueError('Invalid waypoint_id')
        elif isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not bounds[0]<=value<=bounds[1]: raise ValueError('Takeoff altitude must be between 3 and 30 m' if kind=='takeoff' else 'Action outside integration envelope')
    if kind in ('takeoff','goto'):
        reason=sensing_reason(v,minimum)
        if reason:raise ValueError(reason)
        if v['battery_pct']<30: raise ValueError('Takeoff and goto require battery at least 30%')
        if not v['ready']: raise ValueError('Fresh ready telemetry required')
        if v['backend']=='mavsdk' and not v['armed']: raise ValueError('Aircraft must be armed locally by the operator')
    if kind=='goto':
        point=next((p for p in waypoints if p['id']==a['waypoint_id']),None)
        if not point: raise ValueError('Unknown waypoint_id; use an operator-configured waypoint')
        if 'home_lat' not in v or 'home_lon' not in v or distance(point['lat'],point['lon'],v['home_lat'],v['home_lon'])>100: raise ValueError('Target outside 100 m integration envelope')
    if kind=='land':
        if 'home_lat' not in v or 'home_lon' not in v or distance(v['lat'],v['lon'],v['home_lat'],v['home_lon'])>radius: raise ValueError('Landing outside home zone; use return_home')
    if kind=='navigate' and (not v['ready'] or v['battery_pct']<30): raise ValueError('Fresh odometry, battery at least 30% and active Nav2 required')
    return dict(a)

def preflight_plan(vehicle,raw,waypoints,radius=12,minimum=10):
    state=dict(vehicle);actions=[]
    for item in raw:
        a=preflight(state,item,waypoints,radius,minimum);actions.append(a)
        if a['type']=='goto':
            point=next(p for p in waypoints if p['id']==a['waypoint_id']);state.update(lat=point['lat'],lon=point['lon'])
        elif a['type']=='return_home' and 'home_lat' in state and 'home_lon' in state:state.update(lat=state['home_lat'],lon=state['home_lon'])
    return actions