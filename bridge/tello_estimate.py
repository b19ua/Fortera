"""Dead-reckoning estimate of a Tello's offset from its takeoff point (protocol §13).

Built only from the bridge's own commanded and acknowledged moves. It is an estimate, never an observed
position: it drifts, grows uncertainty with every step and is invalidated whenever the bridge can no longer
account for the aircraft's motion. Once invalid it stays invalid until the next takeoff resets it.
"""
import math

INVALID_REASONS=('stop','interrupted','telemetry_gap','yaw_jump','reconnect','unconfirmed_action','sdk_error','not_airborne')

class EstimateTracker:
    def __init__(self,base_uncertainty_m=0.1,per_metre=0.15,per_turn_deg=0.002,yaw_jump_deg=20,gap_s=1.0,unconfirmed_speed_cm_s=25,grace_s=1.5):
        self.base=base_uncertainty_m;self.per_metre=per_metre;self.per_turn=per_turn_deg
        self.yaw_jump_deg=yaw_jump_deg;self.gap_s=gap_s;self.unconfirmed_speed=unconfirmed_speed_cm_s;self.grace_s=grace_s;self.grace_until=0.0
        self.valid=False;self.reason='not_airborne';self.x=0.0;self.y=0.0;self.yaw=0.0;self.unc=0.0;self.steps=0
        self.ref_yaw=None   # SDK yaw at takeoff (deg)
        self.commanding=False
    def snapshot(self):
        out=dict(kind='dead_reckoning_commanded',valid=self.valid,x_m=round(self.x,3),y_m=round(self.y,3),yaw_deg=round(self.yaw,1),uncertainty_m=round(self.unc,3),steps_since_reset=self.steps)
        if not self.valid: out['invalid_reason']=self.reason
        return out
    def invalidate(self,reason):
        if reason not in INVALID_REASONS: raise ValueError(reason)
        if self.valid: self.valid=False;self.reason=reason
    def reset_at_takeoff(self,sdk_yaw_deg):
        self.valid=True;self.reason=None;self.x=self.y=self.yaw=0.0;self.unc=self.base;self.steps=0;self.ref_yaw=sdk_yaw_deg
    def begin_command(self): self.commanding=True
    def end_command(self,now): self.commanding=False;self.grace_until=now+self.grace_s
    def apply_step(self,x_m,y_m,yaw_rad):
        """Body-frame step (x forward, y left, then a counter-clockwise turn) after the SDK acknowledged it."""
        if not self.valid: return
        h=math.radians(self.yaw)
        self.x+=x_m*math.cos(h)-y_m*math.sin(h);self.y+=x_m*math.sin(h)+y_m*math.cos(h)
        turn=math.degrees(yaw_rad);self.yaw=(self.yaw+turn+180)%360-180
        self.unc+=self.per_metre*math.hypot(x_m,y_m)+self.per_turn*abs(turn)*max(1.0,math.hypot(self.x,self.y));self.steps+=1
    def returned_home(self):
        if self.valid: self.unc+=self.per_metre*math.hypot(self.x,self.y);self.x=self.y=0.0;self.steps+=1
    def observe_state(self,state,age_s,airborne,now):
        """Check SDK state against what the bridge commanded. Any unexplained change invalidates the estimate."""
        if not self.valid: return
        if not airborne: return self.invalidate('not_airborne')
        if age_s>self.gap_s: return self.invalidate('telemetry_gap')
        quiet=not self.commanding and now>=self.grace_until  # deceleration after a step is expected
        if self.ref_yaw is not None and quiet and 'yaw' in state:
            expected=(self.ref_yaw-self.yaw+180)%360-180  # SDK yaw grows clockwise; the estimate is counter-clockwise
            diff=abs((state['yaw']-expected+180)%360-180)
            if diff>self.yaw_jump_deg: return self.invalidate('yaw_jump')
        # SDK vgx/vgy assumed dm/s (verify on the aircraft); x10 gives cm/s.
        if quiet and math.hypot(state.get('vgx',0),state.get('vgy',0))*10>self.unconfirmed_speed: return self.invalidate('unconfirmed_action')