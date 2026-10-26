import pytest
from fastapi.testclient import TestClient
from backend.app import db as database
from backend.app.main import app

@pytest.fixture
def database_path(tmp_path,monkeypatch):
    monkeypatch.setenv('ALFA_SKIP_REFERENCE_CORPUS','1')
    monkeypatch.setenv('ALFA_COLLECTION_WORKER','0')
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'test.sqlite3')
    database.migrate()
    return database.DB_PATH
@pytest.fixture
def client(database_path):
    with TestClient(app) as c:
        me=c.get('/api/v1/me').json();c.headers['X-CSRF-Token']=me['csrf_token']
        yield c

@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    import socket
    def forbidden(*args,**kwargs):
        raise AssertionError('External network is forbidden in unit tests; use a transport double')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
