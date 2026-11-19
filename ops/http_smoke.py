"""Actual Uvicorn TCP/static/auth smoke, without pretending PostgreSQL exists."""
import os
import subprocess
import sys
import time
import httpx
from src.control import free_port


def main():
    port=free_port(); env=dict(os.environ)
    env['DCP_ADMIN_TOKEN']='r12-local-admin'
    env.pop('DATABASE_URL',None)
    child=subprocess.Popen([sys.executable,'-m','uvicorn','src.pg_api:app',
        '--host','127.0.0.1','--port',str(port)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        base=f'http://127.0.0.1:{port}'
        deadline=time.monotonic()+7
        while time.monotonic()<deadline:
            if child.poll() is not None:raise AssertionError('uvicorn quit before ready')
            try:
                r=httpx.get(base+'/',timeout=.5)
                if r.status_code==200:break
            except httpx.HTTPError:time.sleep(.04)
        else:raise AssertionError('server not ready')
        assert 'react.production.min.js' in r.text
        assert httpx.get(base+'/assets/app.js').status_code==200
        assert httpx.get(base+'/assets/app.css').status_code==200
        assert httpx.get(base+'/api/services').status_code==403
        assert httpx.get(base+'/api/services',headers={'X-Admin-Token':'r12-local-admin'}).status_code==503
        print('REAL_FASTAPI_TCP_STATIC_AND_AUTH=PASS')
        print('POSTGRESQL_DATABASE_EXECUTION=NOT_RUN')
    finally:
        child.terminate()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)

if __name__=='__main__':main()
