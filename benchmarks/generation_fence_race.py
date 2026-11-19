"""Deterministic native-SQLite TOCTOU regression against legacy controller.

Two generations pass a SELECT check before the newer commits. On legacy source
lower epoch can overwrite higher; atomic UPSERT rejects that regression.
"""
from __future__ import annotations
import importlib.util,threading,time,tempfile,pathlib,json
from concurrent.futures import ThreadPoolExecutor
from src.effect_outbox import EffectCoordinator

class Proxy:
    def __init__(self, conn, barrier, slow_thread, legacy):
        self.conn=conn;self.barrier=barrier;self.slow_thread=slow_thread;self.legacy=legacy
    def __enter__(self):self.conn.__enter__();return self
    def __exit__(self,*a):return self.conn.__exit__(*a)
    def execute(self,sql,args=()):
        cur=self.conn.execute(sql,args)
        # Legacy: pause AFTER both versions have read the old generation.
        if (self.legacy and self.barrier and sql.startswith('SELECT generation FROM workflow_epoch')
            and threading.current_thread().name in ('high','low')):
            self.barrier.wait(timeout=3)
            if threading.current_thread().name==self.slow_thread:time.sleep(.1)
        return cur
    def executescript(self,sql):return self.conn.executescript(sql)

class AtomicOrderProxy:
    def __init__(self, conn, barrier):self.conn=conn;self.barrier=barrier
    def __enter__(self):self.conn.__enter__();return self
    def __exit__(self,*args):return self.conn.__exit__(*args)
    def execute(self,sql,args=()):
        if (sql.startswith('INSERT INTO workflow_epoch(')
            and threading.current_thread().name in ('high','low')):
            self.barrier.wait(timeout=3)
            if threading.current_thread().name=='low':time.sleep(.1)
        return self.conn.execute(sql,args)
    def executescript(self,sql):return self.conn.executescript(sql)

class Hook:
    def __init__(self,klass,path):
        self.real=klass(path);self.barrier=None
    def set_hook(self,legacy):
        orig=self.real.db;barrier=threading.Barrier(2)
        if legacy:
            self.real.db=lambda:Proxy(orig(),barrier,'low',True)
        else:
            # Atomic SQL checks and commits in one statement. Schedule lower
            # epoch behind the higher one before entering that statement.
            def hooked_db():
                cn=orig()
                return AtomicOrderProxy(cn,barrier)
            self.real.db=hooked_db
    def update(self,epoch,name):
        result={}
        def thread_fn():
            try:self.real.change_generation('robot',epoch);result['ok']=True
            except Exception as ex:result['error']=str(ex)
        t=threading.Thread(target=thread_fn,name=name);t.start();return t,result
    def final(self):
        with self.real.db() as c:
            return c.execute('SELECT generation FROM workflow_epoch WHERE workflow=?',('robot',)).fetchone()[0]

def run_case(klass,path,barrier):
    r=Hook(klass,path);r.real.change_generation('robot',1)
    if barrier:r.set_hook(legacy=(klass is not EffectCoordinator))
    low,lr=r.update(2,'low');hi,hr=r.update(3,'high')
    low.join(4);hi.join(4)
    if low.is_alive() or hi.is_alive():raise RuntimeError('race did not finish')
    return {'initial':1,'final':r.final(),'low':lr,'high':hr}

def main():
    target=pathlib.Path(__file__).parent/'baselines/effect_outbox_before_fencing.py'
    spec=importlib.util.spec_from_file_location('legacy_effect_outbox',target)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as d:
        old=run_case(module.EffectCoordinator,pathlib.Path(d)/'old.sqlite',True)
        new=run_case(EffectCoordinator,pathlib.Path(d)/'new.sqlite',True)
    return {'legacy':old,'fixed':new,'native_sqlite':True,
            'repro_design':'legacy both SELECTs observe epoch 1; fixed low atomic statement enters after high commit',
            'claim':'legacy generation regresses from 3 to 2; atomic UPSERT preserves 3'}

if __name__=='__main__':print(json.dumps(main(),indent=2))
