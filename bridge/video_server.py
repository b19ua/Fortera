"""Loopback MJPEG + latest-frame server for the Tello video (protocol §12). Display/analysis only; never a command channel.

GET /video      multipart MJPEG for the Live Drone page's <img>.
GET /frame.jpg  newest JPEG with X-Fortera-Frame-Seq and X-Fortera-Frame-Age-Ms headers (the bridge's own clock).
Cross-origin reads are allowed only for origins in the allowlist, including Chrome's Private Network Access preflight.
"""
import asyncio, time
try:
    import cv2
except ImportError:
    cv2=None

EXPOSE='X-Fortera-Frame-Seq, X-Fortera-Frame-Age-Ms'

class VideoServer:
    def __init__(self,source,origins,host='127.0.0.1',port=8766,fps=15,quality=70):
        if host not in ('127.0.0.1','::1','localhost'): raise ValueError('The video server binds to loopback only')
        self.source=source;self.origins=set(origins);self.host=host;self.port=port;self.period=1/fps;self.quality=quality
        self.cache=(None,None);self.server=None
    async def start(self): self.server=await asyncio.start_server(self.handle,self.host,self.port)
    async def close(self):
        if self.server: self.server.close();await self.server.wait_closed()
    def jpeg(self):
        frame,seq,at=self.source.latest_frame()
        if frame is None or cv2 is None: return None,seq,at
        if self.cache[0]!=seq:
            ok,buf=cv2.imencode('.jpg',frame,[int(cv2.IMWRITE_JPEG_QUALITY),self.quality])
            self.cache=(seq,buf.tobytes() if ok else None)
        return self.cache[1],seq,at
    def cors(self,origin,preflight=False,private=False):
        if not origin or origin not in self.origins: return []
        h=[('Access-Control-Allow-Origin',origin),('Vary','Origin'),('Access-Control-Expose-Headers',EXPOSE)]
        if preflight: h+=[('Access-Control-Allow-Methods','GET'),('Access-Control-Max-Age','600')]
        if private: h.append(('Access-Control-Allow-Private-Network','true'))
        return h
    async def handle(self,reader,writer):
        try:
            head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),5)
            if len(head)>8192: raise ValueError()
            lines=head.decode('latin-1').split('\r\n');method,path,_=lines[0].split(' ',2)
            headers={k.strip().lower():v.strip() for k,v in (l.split(':',1) for l in lines[1:] if ':' in l)}
            origin=headers.get('origin')
            if origin and origin not in self.origins: return await self.reply(writer,'403 Forbidden',[],b'Origin not allowed')
            if method=='OPTIONS': return await self.reply(writer,'204 No Content',self.cors(origin,True,headers.get('access-control-request-private-network')=='true'),b'')
            if method!='GET': return await self.reply(writer,'405 Method Not Allowed',[],b'')
            path=path.split('?',1)[0]
            if path=='/frame.jpg':
                data,seq,at=self.jpeg()
                if data is None: return await self.reply(writer,'503 Service Unavailable',self.cors(origin),b'No frame yet')
                age=round((time.monotonic()-at)*1000)
                return await self.reply(writer,'200 OK',self.cors(origin)+[('Content-Type','image/jpeg'),('Cache-Control','no-store'),('X-Fortera-Frame-Seq',str(seq)),('X-Fortera-Frame-Age-Ms',str(age))],data)
            if path=='/video':
                writer.write(('HTTP/1.1 200 OK\r\n'+''.join(f'{k}: {v}\r\n' for k,v in self.cors(origin)+[('Content-Type','multipart/x-mixed-replace; boundary=frame'),('Cache-Control','no-store'),('Connection','close')])+'\r\n').encode())
                last=None
                while True:
                    data,seq,_=self.jpeg()
                    if data is not None and seq!=last:
                        last=seq;writer.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data+b'\r\n');await writer.drain()
                    await asyncio.sleep(self.period)
            return await self.reply(writer,'404 Not Found',[],b'')
        except (asyncio.IncompleteReadError,asyncio.TimeoutError,ConnectionError,ValueError): pass
        finally:
            try: writer.close()
            except Exception: pass
    async def reply(self,writer,status,headers,body):
        writer.write((f'HTTP/1.1 {status}\r\n'+''.join(f'{k}: {v}\r\n' for k,v in headers)+f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode()+body)
        await writer.drain()