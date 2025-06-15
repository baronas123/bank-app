from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def signup_user(username: str, password: str):
    return client.post("/signup", data={"username": username, "password": password})


def login_user(username: str, password: str):
    return client.post("/token", data={"username": username, "password": password})


def test_signup_and_login():
    signup = signup_user("alice", "secret")
    assert signup.status_code == 200
    login = login_user("alice", "secret")
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert token == "alice"


def test_topup_and_session():
    signup_user("bob", "pwd")
    login = login_user("bob", "pwd")
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    topup = client.post("/topup", params={"amount": 5}, headers=headers)
    assert topup.status_code == 200
    session_start = client.post("/session/start", headers=headers)
    assert session_start.status_code == 200
    session_id = session_start.json()["session_id"]
    stop = client.post("/session/stop", params={"session_id": session_id, "energy": 1}, headers=headers)
    assert stop.status_code == 200
    assert stop.json()["remaining_balance"] < 5
