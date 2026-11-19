from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os
from pathlib import Path
from .control import Controller
from .recovery import reconcile_deployment,recover

app=FastAPI(title='Deployment Control Plane')
def controller():return Controller(os.environ.get('CONTROL_DB','./deployment.db'))
class Request(BaseModel):
    service:str
    version:str
    idempotency_key:str
@app.post('/deploy')
def deploy(req:Request):
    try:return {'generation':controller().deploy(req.service,req.version,req.idempotency_key)}
    except ValueError as e:raise HTTPException(400,str(e))
@app.post('/reconcile')
def reconcile():return {'result':reconcile_deployment(controller())}
@app.post('/recover')
def recovery():return {'result':recover(controller())}
@app.get('/status/{service}')
def status(service:str):return controller().status(service)
@app.get('/history')
def history():return controller().audit()
@app.get('/',response_class=HTMLResponse)
def ui():return (Path(__file__).parents[1]/'web'/'index.html').read_text()

# Restricted worker API for explicitly queued durable effects. Requires a
# deployment-provided token; do not expose over public plaintext networks.
from fastapi import Header
from hmac import compare_digest
from .lease_claim import LeasedCoordinator

def _effect_coordinator():
    return LeasedCoordinator(os.environ.get('EFFECT_DB','./deployment_effects.db'))

def _require_worker(token: str | None):
    expected=os.environ.get('WORKER_INTERNAL_TOKEN')
    if not expected:
        raise HTTPException(503,'worker API disabled: missing WORKER_INTERNAL_TOKEN')
    if token is None or not compare_digest(token,expected):
        raise HTTPException(403,'unauthorized worker')

class LeaseClaim(BaseModel):
    effect_id:str
    lease_seconds:float=30.

class LeaseAck(BaseModel):
    effect_id:str
    token:str

@app.post('/internal/effects/claim')
def claim_effect(req:LeaseClaim,x_worker_token:str|None=Header(default=None)):
    _require_worker(x_worker_token)
    try:token=_effect_coordinator().claim_leased(req.effect_id,req.lease_seconds)
    except ValueError as exc:raise HTTPException(409,str(exc))
    return {'effect_id':req.effect_id,'token':token,'claimed':token is not None}

@app.post('/internal/effects/ack')
def ack_effect(req:LeaseAck,x_worker_token:str|None=Header(default=None)):
    _require_worker(x_worker_token)
    try:_effect_coordinator().ack_leased(req.effect_id,req.token)
    except ValueError as exc:raise HTTPException(409,str(exc))
    return {'effect_id':req.effect_id,'acknowledged':True}
