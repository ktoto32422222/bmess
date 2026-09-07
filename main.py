import os
import sqlite3
import hashlib
import smtplib
import json
from datetime import datetime
from email.mime.text import MIMEText
from flask import Flask, request, jsonify

# ==============================================================================
# ИНИЦИАЛИЗАЦИЯ FLASK
# ==============================================================================
app = Flask(__name__)

# НАСТРОЙКИ ДЛЯ RENDER
PORT = int(os.environ.get("PORT", 10000))  # Render использует порт 10000
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "akkdlymine4@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "zjqf eony jebn kfqr")

DB_NAME = "messenger.db"
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT UNIQUE, password TEXT, avatar TEXT, notifications INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS chats 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, name TEXT, avatar TEXT, owner_id INTEGER, parent_channel_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS chat_members 
                 (chat_id INTEGER, user_id INTEGER, role TEXT, PRIMARY KEY(chat_id, user_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender_id INTEGER, content TEXT, file_path TEXT, timestamp TEXT, is_pinned INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS verification_codes 
                 (email TEXT PRIMARY KEY, code TEXT, timestamp REAL)''')
    conn.commit()
    print("✅ База данных инициализирована")
    return conn

# Инициализируем БД при старте
init_db()

def send_email(email, code):
    try:
        msg = MIMEText(f"Ваш код подтверждения для мессенджера: {code}")
        msg['Subject'] = "Код подтверждения"
        msg['From'] = SMTP_EMAIL
        msg['To'] = email
        print(f"[SMTP] Отправка на {email}...")
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[SMTP] ✅ Успешно отправлено на {email}")
        return True
    except Exception as e:
        print(f"[SMTP] ❌ Ошибка: {e}")
        return False

def process_request(req: dict, user_id: int):
    action = req.get("action")
    c = get_db().cursor()
    
    if action == "send_code":
        email = req.get("email")
        code = str(hashlib.sha256(os.urandom(8)).hexdigest())[:6]
        c.execute("INSERT OR REPLACE INTO verification_codes (email, code, timestamp) VALUES (?, ?, ?)", 
                  (email, code, datetime.now().timestamp()))
        get_db().commit()
        if send_email(email, code):
            return {"status": "success", "message": "Код отправлен на почту"}
        return {"status": "error", "message": "Ошибка отправки письма. См. логи сервера."}
        
    elif action == "register":
        email, password, username, code = req.get("email"), req.get("password"), req.get("username"), req.get("code")
        c.execute("SELECT code FROM verification_codes WHERE email=? ORDER BY timestamp DESC LIMIT 1", (email,))
        row = c.fetchone()
        if not row or row["code"] != code or (datetime.now().timestamp() - row["timestamp"]) > 300:
            return {"status": "error", "message": "Неверный или просроченный код"}
        
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()
        try:
            c.execute("INSERT INTO users (username, email, password) VALUES (?, ?, ?)", (username, email, pwd_hash))
            get_db().commit()
            return {"status": "success", "message": "Регистрация успешна"}
        except sqlite3.IntegrityError:
            return {"status": "error", "message": "Email или имя пользователя уже заняты"}

    elif action == "login":
        email, password = req.get("email"), req.get("password")
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()
        c.execute("SELECT id, username, avatar, notifications FROM users WHERE email=? AND password=?", (email, pwd_hash))
        user = c.fetchone()
        if user:
            return {"status": "success", "user_id": user["id"], "user": dict(user)}
        return {"status": "error", "message": "Неверный email или пароль"}

    elif action == "get_chats":
        uid = req.get("user_id")
        c.execute('''SELECT c.*, cm.role FROM chats c JOIN chat_members cm ON c.id = cm.chat_id WHERE cm.user_id=?''', (uid,))
        return {"status": "success", "chats": [dict(row) for row in c.fetchall()]}

    elif action == "search_users":
        query = req.get("query")
        c.execute("SELECT id, username FROM users WHERE username LIKE ?", (f"%{query}%",))
        return {"status": "success", "users": [dict(row) for row in c.fetchall()]}

    elif action == "create_channel":
        uid = req.get("user_id")
        name = req.get("name")
        c.execute("INSERT INTO chats (type, name, owner_id) VALUES ('channel', ?, ?)", (name, uid))
        channel_id = c.lastrowid
        c.execute("INSERT INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'owner')", (channel_id, uid))
        
        chat_name = f"@{name}_chat"
        c.execute("INSERT INTO chats (type, name, owner_id, parent_channel_id) VALUES ('group', ?, ?, ?)", (chat_name, uid, channel_id))
        group_id = c.lastrowid
        c.execute("INSERT INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'owner')", (group_id, uid))
        get_db().commit()
        return {"status": "success", "message": "Канал и чат созданы", "channel_id": channel_id, "group_id": group_id}

    elif action == "send_message":
        uid = req.get("user_id")
        chat_id, content, file_path = req.get("chat_id"), req.get("content"), req.get("file_path")
        is_pinned = req.get("is_pinned", 0)
        
        c.execute("SELECT type FROM chats WHERE id=?", (chat_id,))
        chat_info = c.fetchone()
        
        c.execute("INSERT INTO messages (chat_id, sender_id, content, file_path, timestamp, is_pinned) VALUES (?, ?, ?, ?, ?, ?)",
                  (chat_id, uid, content, file_path, datetime.now().isoformat(), is_pinned))
        get_db().commit()
        
        c.execute("SELECT * FROM messages WHERE id=?", (c.lastrowid,))
        msg = dict(c.fetchone())
        c.execute("SELECT username FROM users WHERE id=?", (uid,))
        msg["sender_name"] = c.fetchone()["username"]
        
        if chat_info and chat_info["type"] == "channel":
            c.execute("SELECT id FROM chats WHERE parent_channel_id=?", (chat_id,))
            group = c.fetchone()
            if group:
                c.execute("INSERT INTO messages (chat_id, sender_id, content, file_path, timestamp, is_pinned) VALUES (?, ?, ?, ?, ?, 1)",
                          (group["id"], uid, content, file_path, datetime.now().isoformat()))
                get_db().commit()

        return {"status": "success", "broadcast_chat_id": chat_id, "message_data": msg}

    elif action == "get_messages":
        chat_id = req.get("chat_id")
        c.execute("SELECT m.*, u.username as sender_name FROM messages m JOIN users u ON m.sender_id = u.id WHERE m.chat_id=? ORDER BY m.timestamp ASC", (chat_id,))
        return {"status": "success", "messages": [dict(row) for row in c.fetchall()]}

    elif action == "update_group_settings":
        uid = req.get("user_id")
        chat_id, new_name, new_avatar = req.get("chat_id"), req.get("name"), req.get("avatar")
        c.execute("SELECT role FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, uid))
        role = c.fetchone()
        if not role or role["role"] not in ("owner", "admin"):
            return {"status": "error", "message": "Недостаточно прав"}
        
        updates, params = [], []
        if new_name: updates.append("name=?"); params.append(new_name)
        if new_avatar: updates.append("avatar=?"); params.append(new_avatar)
        
        if updates:
            params.append(chat_id)
            c.execute(f"UPDATE chats SET {', '.join(updates)} WHERE id=?", params)
            get_db().commit()
        return {"status": "success", "message": "Настройки обновлены"}

    elif action == "manage_member":
        uid = req.get("user_id")
        chat_id, target_user_id, action_type = req.get("chat_id"), req.get("target_user_id"), req.get("action")
        c.execute("SELECT role FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, uid))
        requester_role = c.fetchone()
        if not requester_role or requester_role["role"] not in ("owner", "admin"):
            return {"status": "error", "message": "Недостаточно прав"}
        
        c.execute("SELECT role FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, target_user_id))
        target_role_row = c.fetchone()
        if not target_role_row:
            return {"status": "error", "message": "Пользователь не в чате"}
        
        if target_role_row["role"] == "owner":
            return {"status": "error", "message": "Владельца нельзя понизить или выгнать"}
        
        if action_type == "kick":
            c.execute("DELETE FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, target_user_id))
        elif action_type == "promote":
            c.execute("UPDATE chat_members SET role='admin' WHERE chat_id=? AND user_id=?", (chat_id, target_user_id))
        elif action_type == "demote":
            c.execute("UPDATE chat_members SET role='member' WHERE chat_id=? AND user_id=?", (chat_id, target_user_id))
        elif action_type == "mute":
            c.execute("UPDATE chat_members SET role='muted' WHERE chat_id=? AND user_id=?", (chat_id, target_user_id))
        get_db().commit()
        return {"status": "success", "message": f"Действие '{action_type}' выполнено"}

    elif action == "update_settings":
        uid = req.get("user_id")
        username, avatar, notifications = req.get("username"), req.get("avatar"), req.get("notifications")
        updates, params = [], []
        if username: updates.append("username=?"); params.append(username)
        if avatar: updates.append("avatar=?"); params.append(avatar)
        if notifications is not None: updates.append("notifications=?"); params.append(notifications)
        
        if updates:
            params.append(uid)
            c.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", params)
            get_db().commit()
        return {"status": "success", "message": "Настройки обновлены"}

    return {"status": "error", "message": "Неизвестное действие"}


# ==============================================================================
# FLASK ROUTES
# ==============================================================================

@app.route('/', methods=['GET'])
def index():
    return jsonify({"status": "ok", "message": "Messenger API Server is Running on Render"})

@app.route('/api', methods=['POST'])
def handle_api_request():
    try:
        req_data = request.get_json()
        if not req_data:
            return jsonify({"status": "error", "message": "Нет данных JSON"}), 400
        
        user_id = req_data.get("user_id")
        response = process_request(req_data, user_id)
        return jsonify(response)
    except Exception as e:
        print(f"Ошибка в /api: {e}")
        return jsonify({"status": "error", "message": f"Ошибка сервера: {str(e)}"}), 500

if __name__ == "__main__":
    print(f"🚀 Запуск сервера на порту {PORT}")
    app.run(host="0.0.0.0", port=PORT)
