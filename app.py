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
CORS(app)

# In-memory state (replace with SQLite for persistence)
rooms = {}       # room_code -> { channel_id, name, owner_token, is_private, banned }
sessions = {}    # session_token -> { room_code, username, webhook_url }

bot = AnomesBot()

def run_bot():
    asyncio.run(bot.start(os.getenv("DISCORD_TOKEN")))

bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()


# ── Rooms ────────────────────────────────────────────────────────────────────

@app.route("/api/rooms/create", methods=["POST"])
def create_room():
    data = request.json
    name = data.get("name", "").strip()
    is_private = data.get("is_private", False)

    if not name:
        return jsonify({"error": "Room name is required"}), 400

    room_code = str(uuid.uuid4())[:8].upper()
    owner_token = str(uuid.uuid4())

    # Bot creates the Discord channel
    channel_id = bot.sync_create_channel(name, is_private, room_code)
    if not channel_id:
        return jsonify({"error": "Failed to create Discord channel"}), 500

    rooms[room_code] = {
        "name": name,
        "channel_id": channel_id,
        "owner_token": owner_token,
        "is_private": is_private,
        "banned": [],        # list of banned usernames
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

    # Create a webhook for this user's session
    webhook_url = bot.sync_create_webhook(room["channel_id"], username)
    if not webhook_url:
        return jsonify({"error": "Failed to create webhook"}), 500

    session_token = str(uuid.uuid4())
    sessions[session_token] = {
        "room_code": room_code,
        "username": username,
        "webhook_url": webhook_url,
    }

    return jsonify({
        "session_token": session_token,
        "username": username,
    })


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

    success = bot.sync_send_webhook(session["webhook_url"], content)
    if not success:
        return jsonify({"error": "Failed to send message"}), 500

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

    # Invalidate all sessions for this username in this room
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

    # Also kick them
    to_remove = [
        t for t, s in sessions.items()
        if s["room_code"] == room_code and s["username"] == target_username
    ]
    for t in to_remove:
        del sessions[t]

    return jsonify({"ok": True, "banned": target_username})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
