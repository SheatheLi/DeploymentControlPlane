"""Separate local HTTP effect receiver with durable receiver-side deduplication.

Receiver persistence uses a separate SQLite DB intentionally: it models an
independent downstream system, NOT a PostgreSQL substitute for the control DB.
"""
import hmac
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

class EffectHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path!='/effects':self.send_error(404);return
        auth=self.headers.get('X-Effect-Token','')
        if not hmac.compare_digest(auth,self.server.effect_token):self.send_error(403);return
        identity=self.headers.get('X-Effect-Id','')
        try:
            length_header=self.headers.get('Content-Length','')
            if not length_header.isdecimal():raise ValueError('bad length header')
            size=int(length_header)
            if not identity.startswith('release:') or not (1<=size<=10000):
                raise ValueError('invalid effect envelope')
            raw=self.rfile.read(size)
            if len(raw)!=size:raise ValueError('truncated effect')
            payload=json.loads(raw,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
            if not isinstance(payload,dict):raise ValueError('effect must be object')
            canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
            with sqlite3.connect(self.server.db_path,timeout=10) as db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT payload FROM applied WHERE effect_id=?',(identity,)).fetchone()
                if old and old[0]!=canonical:
                    self.send_error(409,'idempotency key payload mismatch');return
                if not old:
                    # Enforce monotonic generation at the external receiver too.
                    service=payload.get('service')
                    generation=payload.get('generation')
                    if (not isinstance(service,str) or not service or
                            type(generation) is not int or generation<1 or
                            identity!=f'release:{service}:{generation}'):
                        self.send_error(400,'invalid release envelope');return
                    latest=db.execute('SELECT generation FROM latest_generation WHERE service=?',
                                      (service,)).fetchone()
                    if latest and generation<latest[0]:
                        self.send_error(409,'stale generation');return
                    db.execute('INSERT INTO applied(effect_id,payload) VALUES (?,?)',(identity,canonical))
                    db.execute('INSERT INTO latest_generation(service,generation) VALUES (?,?) '
                               'ON CONFLICT(service) DO UPDATE SET generation=excluded.generation '
                               'WHERE excluded.generation>latest_generation.generation',
                               (service,generation))
            answer=json.dumps({'status':'accepted','effect_id':identity,'duplicate':bool(old)}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(answer)))
            self.end_headers();self.wfile.write(answer)
        except (ValueError,TypeError,sqlite3.Error):self.send_error(400)
    def do_GET(self):
        if self.path!='/health':self.send_error(404);return
        payload=b'{"status":"healthy"}'
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    def log_message(self,*a):pass


def make_server(port:int,db_path:str,token:str):
    if not token:raise ValueError('effect token is required')
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS applied(effect_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS latest_generation(service TEXT PRIMARY KEY,generation INTEGER NOT NULL)')
    server=ThreadingHTTPServer(('127.0.0.1',port),EffectHandler)
    server.effect_token=token
    server.db_path=db_path
    return server

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--db',default='./receiver.db')
    x=p.parse_args()
    make_server(x.port,x.db,os.environ['DCP_EFFECT_TOKEN']).serve_forever()
