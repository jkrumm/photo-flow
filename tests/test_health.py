from fastapi.testclient import TestClient

from photo_flow.api.app import create_app


def test_health():
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
