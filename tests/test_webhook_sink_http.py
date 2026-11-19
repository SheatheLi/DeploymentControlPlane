"""Real HTTP receiver tests, including malformed-wire cases and durable fences."""
from contextlib import contextmanager
import http.client,json,threading
from src.webhook_sink import make_server

@contextmanager
def receiver(path):
    s=make_server(0,str(path),'known-secret')
    worker=threading.Thread(target=s.serve_forever,daemon=True);worker.start()
    try:yield s.server_address[1]
    finally:s.shutdown();s.server_close();worker.join(2)

def post(port,identity,body=b'{}',length=None,token='known-secret'):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    headers={'X-Effect-Id':identity,'X-Effect-Token':token,'Content-Type':'application/json',
             'Content-Length':str(len(body)) if length is None else length}
    conn.request('POST','/effects',body=body,headers=headers)
    res=conn.getresponse();data=res.read();result=(res.status,data)
    conn.close();return result

def envelope(version,service='svc'):
    return json.dumps({'service':service,'generation':version,'version':f'v{version}'},separators=(',',':')).encode()

def test_durable_fencing_real_http(tmp_path):
    db=tmp_path/'receiver.db'
    with receiver(db) as port:
        assert post(port,'release:svc:2',envelope(2))[0]==200
        assert post(port,'release:svc:1',envelope(1))[0]==409
        assert post(port,'release:svc:2',envelope(2))[0]==200
        assert post(port,'release:svc:2',envelope(2,'other'))[0]==409
        assert post(port,'release:svc:3',envelope(3))[0]==200
    with receiver(db) as port:
        assert post(port,'release:svc:2',envelope(2))[0]==200  # harmless old duplicate
        assert post(port,'release:svc:1',envelope(1))[0]==409

def test_untrusted_request_headers_are_rejected_without_killing_server(tmp_path):
    with receiver(tmp_path/'sink.db') as port:
        assert post(port,'release:svc:1',envelope(1),length='NaN')[0]==400
        assert post(port,'release:svc:1',b'{"service":"svc","generation":NaN}')[0]==400
        assert post(port,'release:svc:1',envelope(1),token='wrong')[0]==403
        assert post(port,'release:svc:1',envelope(1))[0]==200
