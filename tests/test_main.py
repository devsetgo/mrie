from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "mikeryan.ie" in response.text
    assert "Connect" in response.text
    assert "LinkedIn" in response.text
    assert "Github" in response.text
    assert "PyPi" in response.text
    assert "Copyright &copy; Mike Ryan" in response.text

def test_error_page():
    error_codes = [400, 404, 500]
    for code in error_codes:
        response = client.get(f"/error/{code}")
        assert response.status_code == 200


def test_health_status():
    response = client.get("/api/health/status")
    assert response.status_code == 200

def test_health_uptime():
    response = client.get("/api/health/uptime")
    assert response.status_code == 200

def test_health_heapdump():
    response = client.get("/api/health/heapdump")
    assert response.status_code == 200