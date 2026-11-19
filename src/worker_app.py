"""Trusted local HTTP worker; optionally self-register a launch capability."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,sys,sqlite3,os,time
port=int(sys.argv[1]);version=sys.argv[2]
nonce=''
if len(sys.argv)>=9:
    _,_,_,db_path,job_id,token,nonce,service,generation=sys.argv[:9]
    with sqlite3.connect(db_path,timeout=5) as db:
        db.execute('''INSERT INTO launches(nonce,job_id,lease_token,service,version,generation,port,pid,state,created_at)
             VALUES (?,?,?,?,?,?,?,?,'REGISTERED',?)''',
             (nonce,int(job_id),int(token),service,version,int(generation),port,os.getpid(),time.time()))
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/health','/'):
            body=json.dumps({'status':'healthy','version':version,'launch_nonce':nonce}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        else:self.send_error(404)
    def log_message(self,*args):pass
if len(sys.argv)>=5 and sys.argv[3]=='--listen-fd':
    import socket
    fd=int(sys.argv[4])
    inherited=socket.fromfd(fd,socket.AF_INET,socket.SOCK_STREAM)
    try:
        host,actual_port=inherited.getsockname()[:2]
        if host!='127.0.0.1' or actual_port!=port or not inherited.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN):
            raise RuntimeError('inherited descriptor is not the expected loopback listener')
        server=ThreadingHTTPServer(('127.0.0.1',port),Handler,bind_and_activate=False)
        server.socket.close()
        server.socket=inherited
        server.server_address=(host,port)
        server.serve_forever()
    finally:
        inherited.close()
else:
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
