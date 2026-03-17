from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, request, send_from_directory, session, redirect, url_for, g

ROOT = Path(__file__).resolve().parent

app = Flask(__name__, static_folder=str(ROOT), static_url_path="")
app.secret_key = os.environ.get("OPENCLAW_MEMO_SECRET", "change-this-secret")

# SQLite database path
DB_PATH = ROOT / "personalmemo.db"

LOGIN_EMAIL = "junichoon@gmail.com"
LOGIN_HASH = "scrypt:32768:8:1$DZYpN0yDDJ9QJ1TF$33df9de05818ac68ed9af4762f41e651815c72e545f6e0c6da1a1f452208d1a35ab758e8d08ce0d02af6285f57f47f306709303ed85a473ee1ff972c17138878"

PUBLIC_PATHS = {
    "/login",
    "/login.html",
    "/login.js",
    "/styles.css",
}


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def _init_db():
    """Initialize the database with required tables."""
    db = sqlite3.connect(str(DB_PATH))
    cursor = db.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            metadata TEXT,
            deleted INTEGER DEFAULT 0,
            deleted_at TEXT
        )
    ''')
    db.commit()
    db.close()


def _load_items() -> List[Dict[str, Any]]:
    """Load all non-deleted memos from the database."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, content, timestamp, metadata, deleted FROM memos WHERE deleted = 0')
    rows = cursor.fetchall()
    items = []
    for row in rows:
        item = {
            "id": row["id"],
            "content": row["content"],
            "timestamp": row["timestamp"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {}
        }
        items.append(item)
    return items


def _save_items(items: List[Dict[str, Any]]) -> None:
    """Save items to database (full sync)."""
    db = get_db()
    cursor = db.cursor()
    
    # Clear existing and re-insert
    cursor.execute('DELETE FROM memos')
    for item in items:
        cursor.execute('''
            INSERT INTO memos (id, content, timestamp, metadata, deleted)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            item.get("id"),
            item.get("content"),
            item.get("timestamp"),
            json.dumps(item.get("metadata", {})),
            item.get("deleted", 0)
        ))
    db.commit()


def _export_memos(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Export memos for API response."""
    payload = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "count": len(items),
        "memos": [
            {
                "id": x.get("id"),
                "content": x.get("content"),
                "timestamp": x.get("timestamp"),
                "metadata": x.get("metadata", {}),
            }
            for x in items
        ],
    }
    # Also save to memos.json for backup/compatibility
    EXPORT_PATH = ROOT / "memos.json"
    EXPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _sync_and_export() -> Dict[str, Any]:
    items = _load_items()
    return _export_memos(items)


@app.before_request
def require_login():
    if request.path in PUBLIC_PATHS or request.path.startswith("/static"):
        return None
    if request.path.startswith("/api/") or request.path.endswith(".html") or request.path == "/":
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
    return None


@app.get("/login")
def login():
    return send_from_directory(ROOT, "login.html")


@app.post("/login")
def login_post():
    data = request.form or {}
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()
    from werkzeug.security import check_password_hash

    if email == LOGIN_EMAIL and check_password_hash(LOGIN_HASH, password):
        session["user"] = email
        next_path = request.args.get("next") or "/"
        return redirect(next_path)

    return redirect(url_for("login") + "?error=1")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/tags.html")
def tags_page():
    return send_from_directory(ROOT, "tags.html")


@app.get("/tags.js")
def tags_script():
    return send_from_directory(ROOT, "tags.js")


@app.get("/login.js")
def login_script():
    return send_from_directory(ROOT, "login.js")


@app.get("/api/memos")
def list_memos():
    payload = _sync_and_export()
    return jsonify(payload)


@app.post("/api/memos")
def create_memo():
    data = request.get_json(force=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400

    db = get_db()
    cursor = db.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    metadata = json.dumps(data.get("metadata", {}))
    
    cursor.execute('''
        INSERT INTO memos (content, timestamp, metadata)
        VALUES (?, ?, ?)
    ''', (content, now, metadata))
    db.commit()
    
    # Get the inserted ID
    memo_id = cursor.lastrowid
    
    items = _load_items()
    return jsonify(_export_memos(items))


@app.put("/api/memos/<int:memo_id>")
def update_memo(memo_id: int):
    data = request.get_json(force=True) or {}
    db = get_db()
    cursor = db.cursor()
    
    # Check if memo exists and not deleted
    cursor.execute('SELECT id FROM memos WHERE id = ? AND deleted = 0', (memo_id,))
    if not cursor.fetchone():
        return jsonify({"error": "memo not found"}), 404
    
    now = datetime.now().isoformat(timespec="seconds")
    metadata = json.dumps(data.get("metadata", {}))
    
    cursor.execute('''
        UPDATE memos
        SET content = ?, timestamp = ?, metadata = ?
        WHERE id = ? AND deleted = 0
    ''', (data.get("content", "").strip(), now, metadata, memo_id))
    db.commit()
    
    items = _load_items()
    return jsonify(_export_memos(items))


@app.delete("/api/memos/<int:memo_id>")
def delete_memo(memo_id: int):
    db = get_db()
    cursor = db.cursor()
    
    # Soft delete
    now = datetime.now().isoformat(timespec="seconds")
    cursor.execute('''
        UPDATE memos
        SET deleted = 1, deleted_at = ?
        WHERE id = ? AND deleted = 0
    ''', (now, memo_id))
    db.commit()
    
    if cursor.rowcount == 0:
        return jsonify({"error": "memo not found"}), 404
    
    items = _load_items()
    return jsonify(_export_memos(items))


if __name__ == "__main__":
    # Initialize database on first run
    _init_db()
    app.run(host="0.0.0.0", port=8080, debug=True)
