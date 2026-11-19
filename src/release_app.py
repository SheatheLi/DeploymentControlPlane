"""Fixed, trusted release probe target; never executes user-supplied commands."""
import json
import socket
import sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

if __name__=='__main__':
    port,version,nonce = int(sys.argv[1]),sys.argv[2],sys.argv[3]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/health','/'):self.send_error(404);return
            raw=json.dumps({'status':'healthy','version':version,'launch_nonce':nonce}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    if len(sys.argv) > 4:
        # POSIX listener inherited from parent: do not race to re-bind a port.
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler,
                                     bind_and_activate=False)
        server.socket.close()  # discard the unused HTTPServer-owned socket
        inherited = socket.socket(fileno=int(sys.argv[4]))
        try:
            host, actual_port = inherited.getsockname()[:2]
            if (inherited.family != socket.AF_INET or host != '127.0.0.1'
                    or actual_port != port or not inherited.getsockopt(
                        socket.SOL_SOCKET, socket.SO_ACCEPTCONN)):
                raise ValueError('inherited descriptor is not the expected loopback listener')
        except Exception:
            inherited.close()
            raise
        server.socket = inherited
    else:
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.serve_forever()
