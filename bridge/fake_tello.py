"""Fake Tello SDK for automated tests (not a flight simulator).

UDP command server (default 127.0.0.1:8889), state broadcaster (to :8890, 10 Hz) and optional synthetic H.264
video to udp://127.0.0.1:11111 via FFmpeg (normal / black / frozen modes). Knobs reproduce the open questions
about a real aircraft: missing `stop`, slow moves, SDK errors, dropped state, EDU mission pads.

  python fake_tello.py --video normal          # then run the gateway with FORTERA_BACKEND=tello FORTERA_TELLO_HOST=127.0.0.1
"""
import argparse, asyncio, math, shutil, subprocess, time

class FakeTello(asyncio.DatagramProtocol):
    def __init__(self,host='127.0.0.1',cmd_port=8889,state_port=8890,supports_stop=True,time_scale=0.02,battery=90,edu=False,pad=-1):
        self.host=host;self.cmd_port=cmd_port;self.state_port=state_port;self.supports_stop=supports_stop;self.scale=time_scale
        self.battery=battery;self.edu=edu;self.pad=pad;self.h=0;self.x=0.0;self.y=0.0;self.yaw=0;self.speed=20;self.vgx=0
        self.flying=False;self.sdk=False;self.log=[];self.motion=None;self.send_state=True;self.fail_next=None;self.temph=60;self.started=time.monotonic()
        self.transport=None;self.state_task=None;self.drift_vgx=0
    async def start(self):
        loop=asyncio.get_running_loop()
        self.transport,_=await loop.create_datagram_endpoint(lambda:self,local_addr=(self.host,self.cmd_port))
        self.state_out,_=await loop.create_datagram_endpoint(asyncio.DatagramProtocol,remote_addr=(self.host,self.state_port))
        self.state_task=asyncio.create_task(self.broadcast())
    def close(self):
        if self.state_task: self.state_task.cancel()
        if self.motion: self.motion.cancel()
        if self.transport: self.transport.close()
        self.state_out.close()
    def state_line(self):
        t=int(time.monotonic()-self.started) if self.flying else 0
        return f"mid:{self.pad if self.edu else -2};x:0;y:0;z:{self.h if self.edu else 0};mpry:0,0,0;pitch:0;roll:0;yaw:{self.yaw};vgx:{self.vgx+self.drift_vgx};vgy:0;vgz:0;templ:{self.temph-2};temph:{self.temph};tof:{max(10,self.h+10) if self.flying else 10};h:{self.h};bat:{self.battery};baro:1.00;time:{t};agx:0.00;agy:0.00;agz:-1000.00;\r\n"
    async def broadcast(self):
        while True:
            if self.send_state: self.state_out.sendto(self.state_line().encode())
            await asyncio.sleep(0.1)
    def datagram_received(self,data,addr):
        cmd=data.decode().strip();self.log.append(cmd)
        asyncio.get_running_loop().create_task(self.handle(cmd,addr))
    def reply(self,text,addr): self.transport.sendto(text.encode(),addr)
    async def move(self,seconds,apply,addr):
        try:
            self.vgx=2;await asyncio.sleep(seconds*self.scale);apply();self.vgx=0;self.reply('ok',addr)
        except asyncio.CancelledError: self.vgx=0
        finally: self.motion=None
    async def handle(self,cmd,addr):
        parts=cmd.split();name=parts[0] if parts else ''
        if self.fail_next and name==self.fail_next: self.fail_next=None;return self.reply('error',addr)
        if name=='command': self.sdk=True;return self.reply('ok',addr)
        if not self.sdk: return
        if name in ('streamon','streamoff'): return self.reply('ok',addr)
        if name=='speed': self.speed=int(parts[1]);return self.reply('ok',addr)
        if name=='battery?': return self.reply(str(self.battery),addr)
        if name=='wifi?': return self.reply('90',addr)
        if name=='sdk?': return self.reply('30' if self.edu else 'unknown command: sdk?',addr)
        if name in ('mon','mdirection'): return self.reply('ok' if self.edu else 'error',addr)
        if name=='rc':
            if self.motion: self.motion.cancel()
            return
        if name=='stop':
            if not self.supports_stop: return self.reply('unknown command: stop',addr)
            if self.motion: self.motion.cancel()
            return self.reply('ok',addr)
        if name=='emergency': self.flying=False;self.h=0;return
        if name=='takeoff':
            def up(): self.flying=True;self.h=80
            self.motion=asyncio.get_running_loop().create_task(self.move(3,up,addr));return
        if name=='land':
            def down(): self.flying=False;self.h=0
            self.motion=asyncio.get_running_loop().create_task(self.move(2,down,addr));return
        if not self.flying: return self.reply('error Not airborne',addr)
        if name in ('forward','back','left','right','up','down'):
            cm=int(parts[1]);h=math.radians(-self.yaw)
            bx,by={'forward':(cm,0),'back':(-cm,0),'left':(0,cm),'right':(0,-cm),'up':(0,0),'down':(0,0)}[name]
            def go():
                self.x+=(bx*math.cos(h)-by*math.sin(h))/100;self.y+=(bx*math.sin(h)+by*math.cos(h))/100
                if name=='up': self.h+=cm
                if name=='down': self.h=max(20,self.h-cm)
            self.motion=asyncio.get_running_loop().create_task(self.move(cm/self.speed,go,addr));return
        if name in ('cw','ccw'):
            deg=int(parts[1])
            def turn(): self.yaw=((self.yaw+(deg if name=='cw' else -deg))+180)%360-180
            self.motion=asyncio.get_running_loop().create_task(self.move(deg/45,turn,addr));return
        if name=='go' and self.edu and len(parts)==6 and parts[5]==f'm{self.pad}':
            self.motion=asyncio.get_running_loop().create_task(self.move(2,lambda:None,addr));return
        self.reply('error',addr)

def start_video(mode='normal',port=11111):
    """Synthetic H.264 like the Tello stream (needs ffmpeg). Returns the process; caller terminates it."""
    if not shutil.which('ffmpeg'): raise RuntimeError('ffmpeg not found')
    src={'normal':'testsrc2=size=960x720:rate=30','black':'color=c=black:size=960x720:rate=30','frozen':'testsrc2=size=960x720:rate=30,trim=end_frame=1,loop=-1:1:0'}[mode]
    return subprocess.Popen(['ffmpeg','-loglevel','error','-re','-f','lavfi','-i',src,'-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-g','30','-f','h264',f'udp://127.0.0.1:{port}'])

async def _main():
    ap=argparse.ArgumentParser();ap.add_argument('--video',choices=['none','normal','black','frozen'],default='none');ap.add_argument('--no-stop',action='store_true');ap.add_argument('--edu',action='store_true')
    a=ap.parse_args();fake=FakeTello(supports_stop=not a.no_stop,time_scale=1,edu=a.edu,pad=1 if a.edu else -1);await fake.start()
    proc=start_video(a.video) if a.video!='none' else None
    print('Fake Tello on 127.0.0.1:8889 (state → :8890)',flush=True)
    try: await asyncio.Future()
    finally:
        fake.close()
        if proc: proc.terminate()
if __name__=='__main__': asyncio.run(_main())