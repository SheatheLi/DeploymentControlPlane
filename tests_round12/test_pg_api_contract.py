from fastapi.testclient import TestClient
from src.pg_api import app
import pytest

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('DCP_ADMIN_TOKEN','localtest')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    return TestClient(app)

def test_without_token_rejected(client):
    assert client.get('/api/services').status_code==403
    assert client.post('/api/deploy',json={'service':'demo','version':'v1','idempotency_key':'a'}).status_code==403

def test_missing_pg_fails_closed_never_falls_back_sqlite(client):
    response=client.get('/api/services',headers={'X-Admin-Token':'localtest'})
    assert response.status_code==503
    assert 'PostgreSQL' in response.text

def test_public_react_ui_served(client):
    page=client.get('/')
    assert page.status_code==200
    assert 'react.production.min.js' in page.text
    assert client.get('/assets/app.js').status_code==200
    assert client.get('/assets/app.css').status_code==200
