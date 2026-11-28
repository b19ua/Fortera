"""Pure reference preflight, shared by gateway and companion backends."""
import json, math
from obstacles import sensing_reason

# DJI Tello behind the local Windows Fortera Tello Bridge: no GPS, no obstacle sensing, no tracking.
# Mirrors src/hardware/tello.ts. navigate on tello is body-relative: x forward, y left (m), yaw counter-clockwise (rad), turn after the move.
TELLO_CAPABILITIES=('hold','land','return_home','takeoff','navigate')
TELLO_LIMITS={'takeoff_min_m':1,'takeoff_max_m':10,'step_min_m':0.2,'step_max_m':5,'envelope_m':15,'airborne_min_m':0.3,'min_battery_pct':30}

# Indoor Demo profile: conservative DEFAULTS mirrored from src/hardware/tello.ts (TELLO_INDOOR_PROFILE). Not validated limits.
TELLO_INDOOR_PROFILE={'id':'indoor_demo','label':'Indoor Demo','require_perception':True,'limits':{
    'takeoff_max_m':1.2,'max_height_m':1.5,'step_min_m':0.2,'step_max_m':0.5,'turn_max_deg':45,'speed_cm_s':20,'envelope_m':2,
    'battery_takeoff_pct':50,'battery_move_pct':40,'temp_max_c':80,'frame_max_age_ms':500,'state_max_age_ms':300,
    'min_confidence':0.6,'settle_ms':1500,'max_flight_s':240}}
TELLO_STANDARD_PROFILE={'id':'standard','label':'Standard (open area)','require_perception':False,'limits':{
    'takeoff_max_m':TELLO_LIMITS['takeoff_max_m'],'max_height_m':TELLO_LIMITS['takeoff_max_m'],'step_min_m':TELLO_LIMITS['step_min_m'],'step_max_m':TELLO_LIMITS['step_max_m'],
    'turn_max_deg':180,'speed_cm_s':50,'envelope_m':TELLO_LIMITS['envelope_m'],'battery_takeoff_pct':TELLO_LIMITS['min_battery_pct'],'battery_move_pct':TELLO_LIMITS['min_battery_pct'],
    'temp_max_c':90,'frame_max_age_ms':1000,'state_max_age_ms':1000,'min_confidence':0,'settle_ms':0,'max_flight_s':900}}
_AT_LEAST={'step_min_m','battery_takeoff_pct','battery_move_pct','min_confidence','settle_ms'}
SETTLED_FLOW_PX=4

def resolve_profile(profile_id='indoor_demo',overrides=None):
    """Profile chosen by the bridge operator (FORTERA_TELLO_PROFILE); overrides may only tighten it."""
    base=TELLO_STANDARD_PROFILE if profile_id=='standard' else TELLO_INDOOR_PROFILE
    limits=dict(base['limits'])
    for k,value in (overrides or {}).items():
        if k not in limits or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): raise ValueError(f'Invalid profile limit {k}')
        limits[k]=max(limits[k],value) if k in _AT_LEAST else min(limits[k],value)
    return {**base,'limits':limits}

def has_mission_pads(v):
    return v.get('backend')=='tello' and (v.get('sdk_state') or {}).get('model')=='tello_edu' and bool((v.get('mission_pad') or {}).get('enabled')) and 'mission_pad' in v.get('capabilities',[])

def effective_capabilities(v):
    caps=v.get('capabilities',[])
    if v.get('backend')!='tello': return caps
    return [c for c in caps if c in TELLO_CAPABILITIES]+(['mission_pad'] if has_mission_pads(v) else [])

def perception_reason(v,profile):
    """Warning-only local camera checks. There is no 'clear' state: passing means no warning, not safe."""
    if not profile: return None
    p=v.get('perception');L=profile['limits']
    if p is None: return 'The bridge does not report local camera checks, so new movement is blocked.' if profile['require_perception'] else None
    if p.get('frame_status')!='ok': return f"Camera frame is {p.get('frame_status')}; new movement is blocked."
    if p.get('frame_age_ms',1e9)>L['frame_max_age_ms'] or p.get('updated_ms_ago',1e9)>L['frame_max_age_ms']: return f"Camera frame is older than {L['frame_max_age_ms']} ms; new movement is blocked."
    if p.get('confidence') is None: return 'Local camera check confidence is unknown; new movement is blocked.'
    if p['confidence']<L['min_confidence']: return f"Local camera check confidence {p['confidence']:.2f} is below {L['min_confidence']}; new movement is blocked."
    if p.get('obstacle_state')=='unknown': return 'Local camera check cannot tell what is ahead (unknown); new movement is blocked.'
    if p.get('obstacle_state')=='warning': return 'Local camera warning: '+(', '.join(f"{w['kind']} ({w['region']})" for w in p.get('warnings',[])) or 'unspecified')+'; new movement is blocked.'
    return None

def sdk_reason(v,profile):
    if not profile: return None
    s=v.get('sdk_state');L=profile['limits']
    if s is None: return 'The bridge does not report Tello SDK state, so new movement is blocked.' if profile['require_perception'] else None
    if s.get('state_age_ms',1e9)>L['state_max_age_ms']: return f"Tello state is older than {L['state_max_age_ms']} ms; new movement is blocked."
    if s.get('temp_high_c') is not None and s['temp_high_c']>=L['temp_max_c']: return f"Tello temperature {s['temp_high_c']} °C is at or above {L['temp_max_c']} °C; land and let it cool."
    if s.get('flight_time_s') is not None and s['flight_time_s']>=L['max_flight_s']: return f"Flight time limit ({L['max_flight_s']} s) reached; land now."
    return None

def estimate_reason(v,profile):
    if not profile: return None
    e=v.get('estimate')
    if e is None: return 'The bridge does not report its estimated offset, so moves are blocked.' if profile['require_perception'] else None
    if not e.get('valid'): return f"Estimated position is invalid ({e.get('invalid_reason','unknown')}); only hold and land are allowed. Land and take off again to reset it."
    return None

def tello_profile_reason(v,a,profile):
    """Mirror of telloProfileReason in src/hardware/perception.ts. land and hold are never blocked here."""
    if not profile or a['type'] in ('hold','land'): return None
    L=profile['limits'];label=profile['label']
    common=perception_reason(v,profile) or sdk_reason(v,profile)
    if a['type']=='takeoff':
        if a['altitude_m']>L['takeoff_max_m']: return f"{label}: takeoff goes only to {L['takeoff_max_m']} m."
        if v['battery_pct']<L['battery_takeoff_pct']: return f"{label}: takeoff needs battery at least {L['battery_takeoff_pct']}%."
        return common
    if common: return common
    flow=(v.get('perception') or {}).get('flow')
    settling=bool(flow) and flow['median_px']>SETTLED_FLOW_PX and L['settle_ms']>0
    if a['type']=='return_home': return estimate_reason(v,profile)
    if a['type']=='pad_step':
        if not has_mission_pads(v): return 'Mission pads need a Tello EDU with pad detection enabled on the bridge.'
        pad=v['mission_pad']
        if pad.get('mid')!=a['mid'] or pad.get('age_ms',1e9)>L['frame_max_age_ms']: return f"Mission pad {a['mid']} is not detected right now."
        corridor=pad.get('corridor') or []
        if corridor and not any(c['from']==a['mid'] or c['to']==a['mid'] for c in corridor): return f"Mission pad {a['mid']} is not part of the configured test corridor."
        if abs(a['x_cm'])>L['step_max_m']*100 or abs(a['y_cm'])>L['step_max_m']*100: return f"{label}: pad offset at most {L['step_max_m']*100:g} cm."
        if a['z_cm']/100>L['max_height_m']: return f"{label}: height above the pad at most {L['max_height_m']} m."
        if v['battery_pct']<L['battery_move_pct']: return f"{label}: moves need battery at least {L['battery_move_pct']}%."
        return 'Still moving (optical flow); wait for the hover to settle.' if settling else None
    if a['type']!='navigate': return None
    est=estimate_reason(v,profile)
    if est: return est
    if v['battery_pct']<L['battery_move_pct']: return f"{label}: moves need battery at least {L['battery_move_pct']}%."
    if v['relative_alt_m']>L['max_height_m']: return f"{label}: height above {L['max_height_m']} m; land first."
    dist=math.hypot(a['x_m'],a['y_m'])
    if profile['id']=='indoor_demo' and a['x_m'] and a['y_m']: return f"{label}: move along one axis per step (forward/back or left/right)."
    if dist>0 and (dist<L['step_min_m']-1e-9 or dist>L['step_max_m']+1e-9): return f"{label}: one step is {L['step_min_m']}–{L['step_max_m']} m."
    if abs(a['yaw_rad'])*180/math.pi>L['turn_max_deg']+1e-6: return f"{label}: turn at most {L['turn_max_deg']}° per step."
    e=v.get('estimate')
    if e and math.hypot(e['x_m'],e['y_m'])+e['uncertainty_m']+dist>L['envelope_m']: return f"{label}: the step could leave the {L['envelope_m']} m area around the estimated takeoff point."
    return 'Still moving (optical flow); wait for the hover to settle.' if settling else None

def tello_navigate_reason(v,a):
    L=TELLO_LIMITS;x,y,yaw=a['x_m'],a['y_m'],a['yaw_rad']
    if abs(x)>L['step_max_m'] or abs(y)>L['step_max_m']: return f"Tello moves at most {L['step_max_m']} m per step on each axis"
    moves=abs(x)>=L['step_min_m'] or abs(y)>=L['step_min_m']
    if not moves and (x or y): return 'Tello moves at least 20 cm per step'
    if not moves and abs(yaw)<math.pi/180: return 'Move or turn must be non-zero'
    if v['relative_alt_m']<L['airborne_min_m']: return 'Tello must be airborne before moving'
    if 'local_x_m' not in v or 'local_y_m' not in v: return 'Tello bridge must report its estimated offset from the takeoff point (local_x_m, local_y_m) before moves'
    if math.hypot(v['local_x_m'],v['local_y_m'])+math.hypot(x,y)>L['envelope_m']: return f"Move would leave the {L['envelope_m']} m area around the takeoff point"
    return None

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

def preflight(v,a,waypoints,radius=12,minimum=10,profile=None):
    if not isinstance(a,dict): raise ValueError('Invalid action')
    kind=a.get('type')
    if kind in ('stop','hover'): return {'type':'hold'}
    if kind=='hold': return {'type':'hold'}
    if kind in ('orbit','follow','approach','inspect'):raise ValueError('Unsupported action: requires verified obstacle avoidance; not enabled')
    tello=v.get('backend')=='tello'
    needed='mission_pad' if kind=='pad_step' else kind
    if needed not in effective_capabilities(v): raise ValueError('Mission pads need a Tello EDU with pad detection enabled on the bridge.' if kind=='pad_step' else 'Tello has no GPS, so it cannot fly to map waypoints.' if tello and kind=='goto' else 'Unsupported action')
    takeoff=(TELLO_LIMITS['takeoff_min_m'],TELLO_LIMITS['takeoff_max_m']) if tello else (3,30)
    fields={'land':{},'return_home':{},'takeoff':{'altitude_m':takeoff},'goto':{'waypoint_id':None},'navigate':{'x_m':(-100,100),'y_m':(-100,100),'yaw_rad':(-math.pi,math.pi)},'pad_step':{'mid':(1,8),'x_cm':(-500,500),'y_cm':(-500,500),'z_cm':(30,500)}}
    if kind not in fields: raise ValueError('Unsupported action')
    if v['backend']=='mavsdk' and (not v['ready'] or not v['armed']): raise ValueError('Fresh telemetry and locally armed aircraft required')
    if set(a)!={'type',*fields[kind]}: raise ValueError('Unexpected action fields')
    for key,bounds in fields[kind].items():
        value=a[key]
        if bounds is None:
            if not isinstance(value,str) or not 1<=len(value)<=64: raise ValueError('Invalid waypoint_id')
        elif isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not bounds[0]<=value<=bounds[1]: raise ValueError((f'Tello takeoff altitude must be between {takeoff[0]} and {takeoff[1]} m' if tello else 'Takeoff altitude must be between 3 and 30 m') if kind=='takeoff' else 'Action outside integration envelope')
    if kind=='pad_step' and any(not isinstance(a[k],int) for k in ('mid','x_cm','y_cm','z_cm')): raise ValueError('Mission pad step fields must be integers')
    if tello:
        reason=tello_profile_reason(v,a,profile)
        if reason: raise ValueError(reason)
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
    if kind=='land' and not tello:
        if 'home_lat' not in v or 'home_lon' not in v or distance(v['lat'],v['lon'],v['home_lat'],v['home_lon'])>radius: raise ValueError('Landing outside home zone; use return_home')
    if kind=='navigate' and tello:
        if not v['ready'] or v['battery_pct']<TELLO_LIMITS['min_battery_pct']: raise ValueError('Fresh Tello telemetry and battery at least 30% required')
        reason=tello_navigate_reason(v,a)
        if reason: raise ValueError(reason)
    elif kind=='navigate' and (not v['ready'] or v['battery_pct']<30): raise ValueError('Fresh odometry, battery at least 30% and active Nav2 required')
    return dict(a)

def preflight_plan(vehicle,raw,waypoints,radius=12,minimum=10,profile=None):
    state=dict(vehicle);actions=[]
    for item in raw:
        a=preflight(state,item,waypoints,radius,minimum,profile);actions.append(a)
        if a['type']=='goto':
            point=next(p for p in waypoints if p['id']==a['waypoint_id']);state.update(lat=point['lat'],lon=point['lon'])
        elif a['type']=='return_home' and 'home_lat' in state and 'home_lon' in state:state.update(lat=state['home_lat'],lon=state['home_lon'])
        if state.get('backend')=='tello':
            if a['type']=='takeoff': state.update(relative_alt_m=a['altitude_m'])
            elif a['type']=='return_home': state.update(local_x_m=0,local_y_m=0)
            elif a['type']=='navigate' and 'local_x_m' in state and 'local_y_m' in state: state.update(local_x_m=math.hypot(state['local_x_m'],state['local_y_m'])+math.hypot(a['x_m'],a['y_m']),local_y_m=0)
    return actions