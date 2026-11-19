import json, tempfile, time, os,signal
from pathlib import Path
from src.control import Controller,run_one,alive
pids=[]
with tempfile.TemporaryDirectory() as tmp:
    c=Controller(str(Path(tmp)/'control.db'))
    lat=[]
    try:
        for i in range(5):
            t=time.perf_counter(); c.deploy('demo',f'version{i}',f'request{i}')
            result=run_one(c);pids.append(result['pid']);lat.append((time.perf_counter()-t)*1000)
        result={'deployments':5,'promotions':sum(x['event']=='PROMOTED' for x in c.audit()),
                'max_request_to_health_ms':max(lat),'latencies_ms':lat,
                'active':c.status('demo')['active']['version'],'backing':'SQLite WAL+real local HTTP subprocess, no Docker/K8s'}
    finally:
        for pid in pids:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
Path('evidence/benchmark.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
