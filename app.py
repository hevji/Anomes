import os
import uuid
import asyncio
import threading
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from bot import AnomesBot

load_dotenv()

app = Flask(__name__)
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else "*"
CORS(app, origins=_origins, supports_credentials=True, allow_headers=["Content-Type", "ngrok-skip-browser-warning"])

# In-memory state
rooms = {}    # room_code -> { channel_id, webhook_url, name, owner_token, is_private, banned, last_message_at }
sessions = {} # session_token -> { room_code, username, last_active_at }

SESSION_TIMEOUT  = 5  * 60  # 5 min  — auto-expire idle sessions
ROOM_TIMEOUT     = 10 * 60  # 10 min — auto-delete rooms with no messages

bot = AnomesBot()

def run_bot():
    asyncio.run(bot.start(os.getenv("DISCORD_TOKEN")))

bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()


# ── Janitor — runs every 60s ──────────────────────────────────────────────────

def janitor():
    while True:
        time.sleep(60)
        now = time.time()

        # Expire idle sessions (webhook cleanup)
        dead_sessions = [t for t, s in sessions.items() if now - s["last_active_at"] > SESSION_TIMEOUT]
        for t in dead_sessions:
            print(f"[Janitor] Expiring session {t[:8]} ({sessions[t]['username']})")
            del sessions[t]

        # Delete rooms with no recent messages
        dead_rooms = [
            code for code, r in rooms.items()
            if now - r["last_message_at"] > ROOM_TIMEOUT
        ]
        for code in dead_rooms:
            room = rooms.pop(code)
            print(f"[Janitor] Deleting idle room {code} ({room['name']})")
            bot.sync_delete_channel(room["channel_id"])

janitor_thread = threading.Thread(target=janitor, daemon=True)
janitor_thread.start()


# ── Rooms ─────────────────────────────────────────────────────────────────────

@app.route("/api/rooms", methods=["GET"])
def list_rooms():
    """Return all public rooms with active user count."""
    public = []
    now = time.time()
    for code, room in rooms.items():
        if room["is_private"]:
            continue
        active_users = len(set(
            s["username"] for s in sessions.values()
            if s["room_code"] == code and now - s["last_active_at"] < SESSION_TIMEOUT
        ))
        public.append({
            "room_code": code,
            "name": room["name"],
            "active_users": active_users,
            "last_message_at": room["last_message_at"],
        })
    # Sort by most recently active
    public.sort(key=lambda r: r["last_message_at"], reverse=True)
    return jsonify({"rooms": public})


@app.route("/api/rooms/create", methods=["POST"])
def create_room():
    data = request.json
    name = data.get("name", "").strip()
    is_private = data.get("is_private", False)

    if not name:
        return jsonify({"error": "Room name is required"}), 400

    room_code = str(uuid.uuid4())[:8].upper()
    owner_token = str(uuid.uuid4())

    channel_id = bot.sync_create_channel(name, is_private, room_code)
    if not channel_id:
        return jsonify({"error": "Failed to create Discord channel"}), 500

    webhook_url = bot.sync_get_webhook_url(channel_id)
    if not webhook_url:
        return jsonify({"error": "Failed to get webhook"}), 500

    rooms[room_code] = {
        "name": name,
        "channel_id": channel_id,
        "webhook_url": webhook_url,
        "owner_token": owner_token,
        "is_private": is_private,
        "banned": [],
        "last_message_at": time.time(),
    }

    return jsonify({"room_code": room_code, "owner_token": owner_token, "is_private": is_private})


@app.route("/api/rooms/<room_code>", methods=["GET"])
def get_room(room_code):
    room = rooms.get(room_code)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    return jsonify({"room_code": room_code, "name": room["name"], "is_private": room["is_private"]})


@app.route("/api/rooms/<room_code>/join", methods=["POST"])
def join_room(room_code):
    room = rooms.get(room_code)
    if not room:
        return jsonify({"error": "Room not found"}), 404

    data = request.json
    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "Username is required"}), 400
    if username in room["banned"]:
        return jsonify({"error": "You are banned from this room"}), 403

    session_token = str(uuid.uuid4())
    sessions[session_token] = {
        "room_code": room_code,
        "username": username,
        "last_active_at": time.time(),
    }

    return jsonify({"session_token": session_token, "username": username})


# ── Messages ──────────────────────────────────────────────────────────────────

@app.route("/api/rooms/<room_code>/messages", methods=["GET"])
def get_messages(room_code):
    room = rooms.get(room_code)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    messages = bot.sync_get_messages(room["channel_id"])
    return jsonify({"messages": messages})


@app.route("/api/rooms/<room_code>/send", methods=["POST"])
def send_message(room_code):
    data = request.json
    session_token = data.get("session_token")
    content = data.get("content", "").strip()

    if not session_token or session_token not in sessions:
        return jsonify({"error": "Invalid session"}), 401

    session = sessions[session_token]
    if session["room_code"] != room_code:
        return jsonify({"error": "Session not for this room"}), 403
    if not content:
        return jsonify({"error": "Message cannot be empty"}), 400

    room = rooms.get(room_code)
    success = bot.sync_send_webhook(room["webhook_url"], content, session["username"])
    if not success:
        return jsonify({"error": "Failed to send message"}), 500

    # Refresh activity timestamps
    sessions[session_token]["last_active_at"] = time.time()
    rooms[room_code]["last_message_at"] = time.time()

    return jsonify({"ok": True})


# ── Owner actions ─────────────────────────────────────────────────────────────

@app.route("/api/rooms/<room_code>/kick", methods=["POST"])
def kick_user(room_code):
    data = request.json
    owner_token = data.get("owner_token")
    target_username = data.get("username")
    room = rooms.get(room_code)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    if room["owner_token"] != owner_token:
        return jsonify({"error": "Not authorized"}), 403
    to_remove = [t for t, s in sessions.items() if s["room_code"] == room_code and s["username"] == target_username]
    for t in to_remove:
        del sessions[t]
    return jsonify({"ok": True, "kicked": target_username})


@app.route("/api/rooms/<room_code>/ban", methods=["POST"])
def ban_user(room_code):
    data = request.json
    owner_token = data.get("owner_token")
    target_username = data.get("username")
    room = rooms.get(room_code)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    if room["owner_token"] != owner_token:
        return jsonify({"error": "Not authorized"}), 403
    if target_username not in room["banned"]:
        room["banned"].append(target_username)
    to_remove = [t for t, s in sessions.items() if s["room_code"] == room_code and s["username"] == target_username]
    for t in to_remove:
        del sessions[t]
    return jsonify({"ok": True, "banned": target_username})


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    app.run(debug=True, port=port)
