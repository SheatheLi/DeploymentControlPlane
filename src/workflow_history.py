"""Transactional workflow event log with deterministic state replay & fencing.

Non-idempotent external side effects cannot be made exactly-once by SQLite events.
"""
import json
import sqlite3
from dataclasses import dataclass

DDL="""CREATE TABLE IF NOT EXISTS workflow_events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, workflow TEXT NOT NULL,
    generation INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS workflow_owners(
    workflow TEXT PRIMARY KEY, generation INTEGER NOT NULL);
"""
@dataclass(frozen=True)
class State:
    generation:int=0
    scheduled:int=0
    completed:int=0
    active:bool=False

def fold(state,event):
    kind=event['kind'];gen=int(event['generation'])
    if gen<state.generation:return state
    if kind=='TAKEOVER':return State(gen,state.scheduled,state.completed,True)
    if gen!=state.generation or not state.active:raise ValueError('event outside active generation')
    if kind=='SCHEDULE':return State(gen,state.scheduled+1,state.completed,True)
    if kind=='COMPLETE':
        if state.completed>=state.scheduled:raise ValueError('unmatched completion')
        return State(gen,state.scheduled,state.completed+1,True)
    if kind=='STOP':return State(gen,state.scheduled,state.completed,False)
    raise ValueError('unknown kind')

class WorkflowHistory:
    def __init__(self,path):
        self.path=str(path)
        with self.open() as db:db.executescript(DDL)
    def open(self):
        db=sqlite3.connect(self.path,isolation_level=None,timeout=5)
        db.execute('PRAGMA journal_mode=WAL')
        return db
    def append(self,workflow,generation,kind,key,payload=None):
        db=self.open()
        try:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT workflow,generation,kind,payload FROM workflow_events WHERE dedup_key=?',(key,)).fetchone()
            raw=json.dumps(payload or {},sort_keys=True)
            if old:
                if old!=(workflow,generation,kind,raw):raise ValueError('idempotency key collision')
                db.commit();return False
            owner=db.execute('SELECT generation FROM workflow_owners WHERE workflow=?',(workflow,)).fetchone()
            if kind=='TAKEOVER':
                if owner is not None and generation<=owner[0]: raise ValueError('stale takeover')
                db.execute('INSERT INTO workflow_owners VALUES (?,?) ON CONFLICT(workflow) DO UPDATE SET generation=excluded.generation',(workflow,generation))
            elif not owner or generation!=owner[0]:raise ValueError('generation fence')
            state=self.replay(workflow,db)
            fold(state,dict(kind=kind,generation=generation)) # state check before committing
            db.execute('INSERT INTO workflow_events(workflow,generation,kind,payload,dedup_key) VALUES (?,?,?,?,?)',(workflow,generation,kind,raw,key))
            db.commit();return True
        except:
            db.rollback();raise
        finally:db.close()
    def replay(self,workflow,db=None):
        owned=db is None
        if owned:db=self.open()
        try:
            state=State()
            for gen,kind in db.execute('SELECT generation,kind FROM workflow_events WHERE workflow=? ORDER BY seq',(workflow,)):
                state=fold(state,dict(generation=gen,kind=kind))
            return state
        finally:
            if owned:db.close()
