import os
import uuid
import asyncio
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from bot import AnomesBot

load_dotenv()

app = Flask(__name__)

_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else "*"
CORS(app, origins=_origins, supports_credentials=True, allow_headers=["Content-Type", "ngrok-skip-browser-warning"])

# In-memory state (replace with SQLite for persistence)
rooms = {}    # room_code -> { channel_id, webhook_url, name, owner_token, is_private, banned }
sessions = {} # session_token -> { room_code, username }

bot = AnomesBot()

def run_bot():
    asyncio.run(bot.start(os.getenv("DISCORD_TOKEN")))

bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()


# Rooms

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

    # Get the webhook that was created with the channel
    webhook_url = bot.sync_get_webhook_url(channel_id)
    if not webhook_url:
        return jsonify({"error": "Failed to get webhook"}), 500

    rooms[room_code] = {
        "name": name,
        "channel_id": channel_id,
        "webhook_url": webhook_url,   # shared webhook for the room
        "owner_token": owner_token,
        "is_private": is_private,
        "banned": [],
    }

    return jsonify({
        "room_code": room_code,
        "owner_token": owner_token,
        "is_private": is_private,
    })


@app.route("/api/rooms/<room_code>", methods=["GET"])
def get_room(room_code):
    room = rooms.get(room_code)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    return jsonify({
        "room_code": room_code,
        "name": room["name"],
        "is_private": room["is_private"],
    })


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

    # No per-user webhook needed — we use the room's shared webhook with username override
    session_token = str(uuid.uuid4())
    sessions[session_token] = {
        "room_code": room_code,
        "username": username,
    }

    return jsonify({
        "session_token": session_token,
        "username": username,
    })


# Messages

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
    # Pass username so Discord shows the alias, not "anomes-hook"
    success = bot.sync_send_webhook(room["webhook_url"], content, session["username"])
    if not success:
        return jsonify({"error": "Failed to send message"}), 500

    return jsonify({"ok": True})


# Owner actions

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

    to_remove = [
        t for t, s in sessions.items()
        if s["room_code"] == room_code and s["username"] == target_username
    ]
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

    to_remove = [
        t for t, s in sessions.items()
        if s["room_code"] == room_code and s["username"] == target_username
    ]
    for t in to_remove:
        del sessions[t]

    return jsonify({"ok": True, "banned": target_username})


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    app.run(debug=True, port=port)
