"""PostgreSQL-only product API: fails closed without credentials or driver."""
import hmac
import os
from pathlib import Path
from fastapi import FastAPI,Header,HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .pg_control import PgControl

app=FastAPI(title='Deployment Control Plane — PostgreSQL')
WEB=Path(__file__).resolve().parents[1]/'web'/'react'
app.mount('/assets',StaticFiles(directory=str(WEB)),name='assets')

def authorized(token):
    expected=os.environ.get('DCP_ADMIN_TOKEN')
    if not expected:raise HTTPException(503,'admin token not configured')
    if token is None or not hmac.compare_digest(token,expected):
        raise HTTPException(403,'invalid admin token')

def db():
    try:return PgControl(os.environ.get('DATABASE_URL',''))
    except (ValueError,RuntimeError) as err:raise HTTPException(503,str(err))

class Deploy(BaseModel):
    service:str=Field(min_length=1,max_length=63)
    version:str=Field(min_length=1,max_length=80)
    idempotency_key:str=Field(min_length=1,max_length=128)

@app.get('/')
def index():return FileResponse(str(WEB/'index.html'))

@app.post('/api/deploy')
def deploy(req:Deploy,x_admin_token:str|None=Header(default=None)):
    authorized(x_admin_token)
    try:generation=db().deploy(req.service,req.version,req.idempotency_key)
    except ValueError as err:raise HTTPException(409,str(err))
    return {'service':req.service,'generation':generation,'status':'PENDING'}

@app.get('/api/services')
def services(x_admin_token:str|None=Header(default=None)):
    authorized(x_admin_token);return db().status()

@app.get('/api/status/{service}')
def status(service:str,x_admin_token:str|None=Header(default=None)):
    authorized(x_admin_token)
    value=db().status(service)
    if value is None:raise HTTPException(404,'unknown service')
    return value

@app.get('/api/audit')
def audit(x_admin_token:str|None=Header(default=None)):
    authorized(x_admin_token);return db().audit()
