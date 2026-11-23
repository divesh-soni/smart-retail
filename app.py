import csv
import io
import os
import secrets
import sys
import time
from datetime import date, timedelta

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import pymysql

import db
import logic

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def env_or_file(name, filename, make):
    # .env wins, otherwise make it once and keep it in a file
    if os.getenv(name):
        return os.getenv(name)
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(make())
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


SIGNUP_CODE = env_or_file("SIGNUP_CODE", ".signup_code", logic.new_recovery_code)

app = Flask(__name__)
app.config.update(
    TEMPLATES_AUTO_RELOAD=True,
    SECRET_KEY=env_or_file("SECRET_KEY", ".secret_key", lambda: secrets.token_hex(32)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

# on a server (gunicorn) main() doesn't run, so create the tables here when asked to
if os.getenv("INIT_DB_ON_START", "").lower() in ("1", "true", "yes"):
    try:
        if db.ensure_database():
            print("Database created with sample products.")
    except pymysql.err.OperationalError as e:
        print(f"Can't connect to MySQL: {e}")


@app.context_processor
def asset_helper():
    # adds ?v=<mtime> so the browser doesn't keep an old css/js
    def asset(filename):
        path = os.path.join(app.static_folder, filename)
        v = int(os.path.getmtime(path)) if os.path.exists(path) else 0
        return url_for("static", filename=filename, v=v)
    return {"asset": asset}


def error(message, status, fields=None):
    return jsonify({"ok": False, "error": message, "fields": fields or {}}), status


@app.errorhandler(pymysql.err.OperationalError)
def db_down(e):
    return error("Can't reach the database. Is MySQL running?", 503)


@app.errorhandler(pymysql.err.IntegrityError)
def duplicate(e):
    return error("This conflicts with an existing record", 409)


@app.errorhandler(500)
def server_error(e):
    return error("Something went wrong. Please try again.", 500)


PUBLIC_ENDPOINTS = {"login", "setup", "signup", "forgot", "static"}

# 5 wrong tries -> locked for 60s
MAX_TRIES, LOCK_SECONDS = 5, 60
FAILED = {}  # key -> (fails, time of last fail)


def locked_for(key):
    count, last = FAILED.get(key, (0, 0))
    wait = int(LOCK_SECONDS - (time.time() - last))
    return wait if count >= MAX_TRIES and wait > 0 else 0


def record_failure(key):
    count, last = FAILED.get(key, (0, 0))
    if time.time() - last > LOCK_SECONDS:
        count = 0
    FAILED[key] = (count + 1, time.time())


def too_many(wait):
    return f"Too many wrong attempts. Please wait {wait} seconds and try again."


def sign_in(username):
    session.clear()
    session["user"] = username
    session.permanent = True


def form_username():
    return request.form.get("username", "").strip().lower()


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if session.get("user"):
        return None
    if request.path.startswith("/api/"):
        return error("Please sign in again", 401)
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if db.count_users() == 0:
        return redirect(url_for("setup"))
    if session.get("user"):
        return redirect(url_for("index"))

    message, username = None, ""
    if request.method == "POST":
        username = form_username()
        wait = locked_for(username)
        if wait:
            message = too_many(wait)
        else:
            user = db.get_user(username)
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                FAILED.pop(username, None)
                sign_in(user["username"])
                return redirect(url_for("index"))
            record_failure(username)
            message = "Wrong username or password."
    return render_template("login.html", error=message, username=username,
                           notice=request.args.get("notice"))


def create_account(username, password):
    code = logic.new_recovery_code()
    db.create_user(username, generate_password_hash(password),
                   generate_password_hash(logic.normalize_code(code)))
    sign_in(username)
    return render_template("recovery.html", code=code, first=True)


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db.count_users() > 0:
        return redirect(url_for("login"))
    errors, username = {}, ""
    if request.method == "POST":
        username = form_username()
        password = request.form.get("password", "")
        errors = logic.validate_account(username, password, request.form.get("confirm", ""))
        if not errors:
            return create_account(username, password)
    return render_template("setup.html", errors=errors, username=username)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if db.count_users() == 0:
        return redirect(url_for("setup"))
    errors, username = {}, ""
    if request.method == "POST":
        username = form_username()
        password = request.form.get("password", "")
        key = "signup:" + (request.remote_addr or "")
        wait = locked_for(key)
        if wait:
            errors["_"] = too_many(wait)
        elif logic.normalize_code(request.form.get("code", "")) != logic.normalize_code(SIGNUP_CODE):
            record_failure(key)
            errors["code"] = "Wrong signup code"
        else:
            errors = logic.validate_account(username, password, request.form.get("confirm", ""))
            if not errors and db.get_user(username):
                errors["username"] = "This username is taken"
            if not errors:
                FAILED.pop(key, None)
                return create_account(username, password)
    return render_template("signup.html", errors=errors, username=username)


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    errors, username = {}, ""
    if request.method == "POST":
        username = form_username()
        code = request.form.get("code", "")
        password = request.form.get("password", "")
        key = "reset:" + username
        user = db.get_user(username)
        wait = locked_for(key)
        if wait:
            errors["_"] = too_many(wait)
        elif not user or not check_password_hash(user["recovery_hash"], logic.normalize_code(code)):
            record_failure(key)
            errors["_"] = "That username and recovery code don't match."
        else:
            errors = logic.validate_password(password, request.form.get("confirm", ""))
            if not errors:
                # new code every reset so the old one can't be reused
                new_code = logic.new_recovery_code()
                db.update_password(username, generate_password_hash(password),
                                   generate_password_hash(logic.normalize_code(new_code)))
                FAILED.pop(key, None)
                sign_in(username)
                return render_template("recovery.html", code=new_code, first=False)
    return render_template("forgot.html", errors=errors, username=username)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login", notice="signed_out"))


@app.post("/api/account/password")
def change_password():
    body = request.get_json(silent=True) or {}
    user = db.get_user(session["user"])
    if not user or not check_password_hash(user["password_hash"], str(body.get("current", ""))):
        return error("Your current password is wrong", 400, {"current": "Wrong password"})
    errors = logic.validate_password(str(body.get("password", "")), str(body.get("confirm", "")))
    if errors:
        return error("Please fix the highlighted fields", 400, errors)
    db.update_password(user["username"], generate_password_hash(body["password"]))
    return jsonify({"ok": True})


def parse_threshold(raw):
    try:
        value = int(raw)
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


def analysis(raw_threshold):
    # bad threshold just falls back to the default here
    t = parse_threshold(raw_threshold)
    return logic.analyze_inventory(db.fetch_all_products(),
                                   logic.DEFAULT_THRESHOLD if t is None else t)


@app.route("/")
def index():
    return render_template("index.html", user=session["user"])


@app.get("/api/products")
def list_products():
    threshold = parse_threshold(request.args.get("threshold", logic.DEFAULT_THRESHOLD))
    if threshold is None:
        return error("threshold must be a whole number >= 0", 400,
                     {"threshold": "Must be a whole number >= 0"})
    return jsonify(logic.analyze_inventory(db.fetch_all_products(), threshold))


@app.post("/api/ask")
def ask():
    body = request.get_json(silent=True) or {}
    question = str(body.get("question", "")).strip()[:300]
    if not question:
        return error("Please type a question", 400, {"question": "Required"})
    result = analysis(body.get("threshold", logic.DEFAULT_THRESHOLD))
    return jsonify(logic.answer_question(question, result))


@app.post("/api/products")
def add_product():
    clean, errors = logic.validate_product(request.get_json(silent=True),
                                           existing_skus=db.fetch_all_skus())
    if errors:
        return error("Please fix the highlighted fields", 400, errors)
    db.insert_product(clean)
    return jsonify({"ok": True, "product": clean}), 201


@app.put("/api/products/<sku>")
def edit_product(sku):
    existing = db.fetch_product(sku)
    if not existing:
        return error(f"Product {sku} not found", 404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Request body must be a JSON object", 400)
    # partial update on top of the saved row, sku stays the same
    merged = {**existing, **data, "sku": existing["sku"]}
    clean, errors = logic.validate_product(merged)
    if errors:
        return error("Please fix the highlighted fields", 400, errors)
    db.update_product(existing["sku"], clean)
    return jsonify({"ok": True, "product": clean})


@app.delete("/api/products/<sku>")
def remove_product(sku):
    if not db.delete_product(sku):
        return error(f"Product {sku} not found", 404)
    return jsonify({"ok": True})


@app.delete("/api/products")
def remove_all_products():
    db.delete_all_products()
    return jsonify({"ok": True})


@app.post("/api/reset")
def reset():
    db.reset_products()
    return jsonify({"ok": True})


def csv_response(rows, name):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    # bom so excel opens it as utf-8
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}-{date.today()}.csv"'})


@app.get("/export/products.csv")
def export_products():
    result = analysis(request.args.get("threshold", logic.DEFAULT_THRESHOLD))
    return csv_response(logic.products_csv_rows(result), "products")


@app.get("/export/orders.csv")
def export_orders():
    result = analysis(request.args.get("threshold", logic.DEFAULT_THRESHOLD))
    return csv_response(logic.orders_csv_rows(result), "order-list")


def main():
    # APP_HOST=0.0.0.0 in .env to open it from a phone on the same wifi
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5000"))

    try:
        if db.ensure_database():
            print("Database created with sample products.")
    except pymysql.err.OperationalError:
        print("Can't connect to MySQL. Start it from XAMPP and check .env")
        sys.exit(1)

    print(f"Smart Retail running at http://127.0.0.1:{port} (Ctrl+C to stop)")
    print(f"Signup code: {SIGNUP_CODE}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
