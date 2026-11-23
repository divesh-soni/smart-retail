import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
import logic  # noqa: E402
from test_logic import SEED  # noqa: E402


# fake db in memory so the tests don't need mysql
class FakeDB:
    def __init__(self):
        self.users = {}
        self.products = [dict(p) for p in SEED]

    def count_users(self):
        return len(self.users)

    def get_user(self, username):
        return self.users.get(username)

    def create_user(self, username, password_hash, recovery_hash):
        self.users[username] = {"username": username, "password_hash": password_hash,
                                "recovery_hash": recovery_hash}

    def update_password(self, username, password_hash, recovery_hash=None):
        self.users[username]["password_hash"] = password_hash
        if recovery_hash:
            self.users[username]["recovery_hash"] = recovery_hash

    def fetch_all_products(self):
        return [dict(p) for p in self.products]


@pytest.fixture
def client(monkeypatch):
    fake = FakeDB()
    for name in ("count_users", "get_user", "create_user", "update_password", "fetch_all_products"):
        monkeypatch.setattr(app_module.db, name, getattr(fake, name))
    app_module.FAILED.clear()
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def create_account(client, username="owner", password="secret123"):
    r = client.post("/setup", data={"username": username, "password": password, "confirm": password})
    assert r.status_code == 200
    return re.search(r'id="recovery-code">([A-Z0-9-]+)<', r.get_data(as_text=True)).group(1)


def sign_in(client, username="owner", password="secret123"):
    return client.post("/login", data={"username": username, "password": password})


def test_first_run_goes_to_setup(client):
    assert client.get("/").headers["Location"].endswith("/login")
    assert client.get("/login").headers["Location"].endswith("/setup")


def test_api_and_exports_need_sign_in(client):
    create_account(client)
    client.post("/logout")
    assert client.get("/api/products").status_code == 401
    assert client.post("/api/ask", json={"question": "what to order"}).status_code == 401
    assert client.get("/export/products.csv").status_code == 302


def test_setup_closes_after_first_account(client):
    create_account(client)
    assert client.get("/setup").headers["Location"].endswith("/login")


def test_setup_validates_input(client):
    r = client.post("/setup", data={"username": "a", "password": "123", "confirm": "456"})
    page = r.get_data(as_text=True)
    assert "Use 3-30 letters" in page and "at least 6 characters" in page and "don&#39;t match" in page


def test_sign_in_and_out(client):
    create_account(client)
    client.post("/logout")
    assert "Wrong username or password" in sign_in(client, password="nope").get_data(as_text=True)
    r = sign_in(client)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    page = client.get("/").get_data(as_text=True)
    assert "owner" in page
    r = client.post("/logout")
    assert "notice=signed_out" in r.headers["Location"]
    assert client.get("/").status_code == 302


def test_username_is_case_insensitive(client):
    create_account(client)
    client.post("/logout")
    assert sign_in(client, username="OWNER").status_code == 302


def test_too_many_wrong_passwords_locks_briefly(client):
    create_account(client)
    client.post("/logout")
    for _ in range(app_module.MAX_TRIES):
        sign_in(client, password="wrong")
    r = sign_in(client)  # right password, still locked
    assert "Too many wrong attempts" in r.get_data(as_text=True)


def test_forgot_password_with_recovery_code(client):
    code = create_account(client)
    client.post("/logout")
    r = client.post("/forgot", data={"username": "owner", "code": code.lower().replace("-", " "),
                                     "password": "newpass1", "confirm": "newpass1"})
    new_code = re.search(r'id="recovery-code">([A-Z0-9-]+)<', r.get_data(as_text=True)).group(1)
    assert new_code != code
    client.post("/logout")
    assert sign_in(client, password="newpass1").status_code == 302
    client.post("/logout")
    # old code shouldn't work anymore
    r = client.post("/forgot", data={"username": "owner", "code": code,
                                     "password": "another1", "confirm": "another1"})
    assert "don&#39;t match" in r.get_data(as_text=True)


def test_forgot_password_wrong_code(client):
    create_account(client)
    client.post("/logout")
    r = client.post("/forgot", data={"username": "owner", "code": "AAAA-BBBB-CCCC",
                                     "password": "newpass1", "confirm": "newpass1"})
    assert "don&#39;t match" in r.get_data(as_text=True)
    assert sign_in(client, password="secret123").status_code == 302


def test_change_password_in_settings(client):
    create_account(client)
    r = client.post("/api/account/password", json={"current": "wrong", "password": "abcdef", "confirm": "abcdef"})
    assert r.status_code == 400
    r = client.post("/api/account/password", json={"current": "secret123", "password": "abcdef", "confirm": "abcdef"})
    assert r.status_code == 200
    client.post("/logout")
    assert sign_in(client, password="abcdef").status_code == 302


def test_export_products_csv(client):
    create_account(client)
    r = client.get("/export/products.csv?threshold=3")
    assert r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"]
    text = r.get_data(as_text=True)
    assert text.startswith("﻿Code,Product,In stock")
    assert "R103,Bread,4,10,1,1,6,0.4,Need ordering,26,Reorder 26 units + sell old batch first" in text


def test_export_orders_csv(client):
    create_account(client)
    lines = client.get("/export/orders.csv").get_data(as_text=True).lstrip("﻿").splitlines()
    assert lines == ["Code,Product,Quantity to order", "R103,Bread,26", "R101,Milk 1L,28",
                     "R105,Yogurt,16", ",Total units,70"]


def test_csv_blocks_formula_injection():
    assert logic.csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert logic.csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert logic.csv_safe("Milk") == "Milk"
    assert logic.csv_safe(5) == 5


def test_recovery_codes():
    code = logic.new_recovery_code()
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
    assert logic.normalize_code(code.lower().replace("-", " ")) == code.replace("-", "")


def test_signup_goes_to_setup_on_first_run(client):
    assert client.get("/signup").headers["Location"].endswith("/setup")


def test_signup_needs_the_code(client, monkeypatch):
    monkeypatch.setattr(app_module, "SIGNUP_CODE", "ABCD-EFGH-JKLM")
    create_account(client)
    client.post("/logout")
    form = {"username": "staff1", "password": "pass123", "confirm": "pass123"}
    r = client.post("/signup", data={**form, "code": "WRONG"})
    assert "Wrong signup code" in r.get_data(as_text=True)
    r = client.post("/signup", data={**form, "username": "owner", "code": "ABCD-EFGH-JKLM"})
    assert "This username is taken" in r.get_data(as_text=True)
    r = client.post("/signup", data={**form, "code": "abcd efgh jklm"})
    assert 'id="recovery-code"' in r.get_data(as_text=True)
    client.post("/logout")
    assert sign_in(client, "staff1", "pass123").status_code == 302
