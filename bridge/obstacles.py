"""Receive-only MAVLink ranges. Monotonic per-source freshness, never navigation."""
import json, math, time

SECTORS=('forward','forward_right','right','back_right','back','back_left','left','forward_left','up','down')
TTL_S=1.0

class ObstacleSensing:
    def __init__(self): self.samples={};self.observed=False
    def ingest(self,name,fields,source=(1,1),now=None):
        now=time.monotonic() if now is None else now
        if not isinstance(fields,dict): return False
        prefix=(source,name)
        def store(key,sector,value):
            self.samples[(*prefix,key)]=(sector,value,now)
        def number(value): return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
        if name=='DISTANCE_SENSOR':
            orientation=fields.get('orientation');sensor=fields.get('id')
            if not isinstance(sensor,int) or isinstance(sensor,bool):return False
            # MAV_SENSOR_ROTATION yaw 0..315; pitch 90=up, pitch 270=down.
            sector=SECTORS[orientation] if isinstance(orientation,int) and not isinstance(orientation,bool) and 0<=orientation<=7 else 'up' if orientation==24 else 'down' if orientation==25 else None
            current=fields.get('current_distance');lo=fields.get('min_distance');hi=fields.get('max_distance')
            valid=sector is not None and all(number(x) for x in (current,lo,hi)) and 0<=lo<=current<=hi and fields.get('signal_quality')!=1
            store(sensor,sector,current/100 if valid else None)
        elif name=='OBSTACLE_DISTANCE':
            values=fields.get('distances');inc=fields.get('increment_f') or fields.get('increment');offset=fields.get('angle_offset',0)
            lo=fields.get('min_distance');hi=fields.get('max_distance')
            if not isinstance(values,list) or len(values)!=72:return False
            # Only BODY_FRD (12) is labelled body-relative. Unsupported frames invalidate prior coverage.
            valid_frame=fields.get('frame',0)==12 and number(inc) and inc!=0 and number(offset) and number(lo) and number(hi) and 0<=lo<=hi<65535
            for i,value in enumerate(values):
                sector=SECTORS[int(math.floor(((offset+i*inc)%360)/45+.5))%8] if valid_frame else None
                if value==65535:
                    # Unused bins carry no coverage; invalidate an existing bin without refreshing it as clear.
                    if (*prefix,i) in self.samples:store(i,sector,None)
                    continue
                valid=valid_frame and number(value) and (value==0 or lo<=value<=hi+1)
                store(i,sector,min(value,hi)/100 if valid else None)
        else:return False
        self.observed=True
        return True
    def snapshot(self,now=None):
        now=time.monotonic() if now is None else now
        if not self.observed:return None
        valid=[(sector,value,max(0,(now-at)*1000)) for sector,value,at in self.samples.values()]
        healthy=bool(valid) and all(sector is not None and value is not None and age<1000 for sector,value,age in valid)
        sectors=[]
        for sector in SECTORS:
            samples=[(value,age) for s,value,age in valid if s==sector]
            if samples:
                fresh=[value for value,age in samples if value is not None and age<1000]
                sectors.append(dict(sector=sector,distance_m=min(fresh) if fresh else None,updated_ms_ago=max(age for _,age in samples)))
        fresh=[value for _,value,age in valid if value is not None and age<1000]
        return dict(healthy=healthy,updated_ms_ago=max((age for _,_,age in valid),default=1000),nearest_m=min(fresh) if fresh else None,sectors=sectors)

async def receive_ranges(system,name,cache):
    """Legacy MAVSDK-Python / mavsdk-grpc API; never sends packets or changes parameters."""
    try:
        async for message in system.mavlink_direct.message(name):
            try: cache.ingest(name,json.loads(message.fields_json),(message.system_id,message.component_id))
            except (ValueError,TypeError,AttributeError): continue
    except Exception:
        # A failed stream must not leave previously valid ranges fresh.
        for key,(sector,_,at) in list(cache.samples.items()):
            if key[1]==name:cache.samples[key]=(sector,None,at)

def sensing_reason(vehicle,minimum=10):
    data=vehicle.get('obstacle_sensing')
    if data is None:return None
    if not data.get('healthy') or data.get('updated_ms_ago',1000)>=1000:return 'obstacle_data_stale'
    nearest=data.get('nearest_m')
    if nearest is not None and nearest<minimum:return 'obstacle_too_close'
    return None