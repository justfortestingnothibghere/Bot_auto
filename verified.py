# verified.py
# Full Verification + Auto-Ban + Admin Activity Monitor
# Designed to work WITHOUT modifying main.py logic

import os
import random
import datetime

CAPTCHA_FOLDER = "captcha"

# Runtime memory
captcha_sessions = {}        # user_id -> captcha_text
admin_watch = set()          # user_ids under live watch

# Dangerous keywords / patterns
BAD_KEYWORDS = [
    "xmrig", "minerd", "cryptonight",
    "ddos", "slowloris", "hping",
    "masscan", "nmap", "zmap",
    "botnet", "malware", "rootkit",
    "chmod +x", "./",
    "curl http", "wget http",
    "nc ", "netcat",
    "bash <(", "sh <(",
    ".bin", "elf",
    "while true", "fork",
    "miner", "stratum+tcp"
]


def attach(bot, conn, admins):
    cur = conn.cursor()

    # -------------------------------------------------
    # /verify COMMAND (Image CAPTCHA)
    # -------------------------------------------------
    @bot.message_handler(commands=['verify'])
    def verify_cmd(message):
        user_id = message.from_user.id

        cur.execute("SELECT verified FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row and row[0] == 1:
            bot.reply_to(message, "✅ You are already verified")
            return

        try:
            img = random.choice(os.listdir(CAPTCHA_FOLDER))
        except:
            bot.reply_to(message, "❌ CAPTCHA folder missing")
            return

        captcha_sessions[user_id] = os.path.splitext(img)[0]

        with open(os.path.join(CAPTCHA_FOLDER, img), "rb") as f:
            bot.send_photo(
                message.chat.id,
                f,
                caption="🛡️ <b>Verification Required</b>\n\nEnter the text shown in the image.",
                parse_mode="HTML"
            )

    # -------------------------------------------------
    # ADMIN: VIEW USER LOGS
    # -------------------------------------------------
    @bot.message_handler(commands=['userlogs'])
    def admin_userlogs(message):
        if message.from_user.id not in admins:
            return

        try:
            target = int(message.text.split()[1])
        except:
            bot.reply_to(message, "❌ Usage: /userlogs <user_id>")
            return

        cur.execute(
            "SELECT activity, timestamp FROM logs WHERE user_id=? ORDER BY timestamp DESC LIMIT 50",
            (target,)
        )
        rows = cur.fetchall()

        if not rows:
            bot.reply_to(message, "ℹ️ No activity found")
            return

        text = f"📜 <b>Last 50 Activities of {target}</b>\n\n"
        for act, ts in rows:
            text += f"• {ts} → <code>{act[:80]}</code>\n"

        bot.reply_to(message, text[:4000], parse_mode="HTML")

    # -------------------------------------------------
    # ADMIN: GLOBAL LOGS
    # -------------------------------------------------
    @bot.message_handler(commands=['alllogs'])
    def admin_alllogs(message):
        if message.from_user.id not in admins:
            return

        cur.execute(
            "SELECT user_id, activity, timestamp FROM logs ORDER BY timestamp DESC LIMIT 30"
        )
        rows = cur.fetchall()

        if not rows:
            bot.reply_to(message, "ℹ️ No logs available")
            return

        text = "📊 <b>GLOBAL ACTIVITY LOG</b>\n\n"
        for uid, act, ts in rows:
            text += (
                f"• <code>{uid}</code> | {ts}\n"
                f"  ↳ <code>{act[:60]}</code>\n\n"
            )

        bot.reply_to(message, text[:4000], parse_mode="HTML")

    # -------------------------------------------------
    # ADMIN: LIVE WATCH
    # -------------------------------------------------
    @bot.message_handler(commands=['livewatch'])
    def livewatch(message):
        if message.from_user.id not in admins:
            return

        try:
            uid = int(message.text.split()[1])
            admin_watch.add(uid)
            bot.reply_to(message, f"👀 Live watch enabled for <code>{uid}</code>", parse_mode="HTML")
        except:
            bot.reply_to(message, "❌ Usage: /livewatch <user_id>")

    @bot.message_handler(commands=['stopwatch'])
    def stopwatch(message):
        if message.from_user.id not in admins:
            return

        try:
            uid = int(message.text.split()[1])
            admin_watch.discard(uid)
            bot.reply_to(message, f"🛑 Live watch stopped for <code>{uid}</code>", parse_mode="HTML")
        except:
            bot.reply_to(message, "❌ Usage: /stopwatch <user_id>")

    # -------------------------------------------------
    # AUTO BAN CORE
    # -------------------------------------------------
    def auto_ban(user_id, command, reason):
        cur.execute("INSERT OR IGNORE INTO bans (user_id) VALUES (?)", (user_id,))
        conn.commit()

        # Log ban
        cur.execute(
            "INSERT INTO logs (user_id, activity, timestamp) VALUES (?, ?, DATETIME('now'))",
            (user_id, f"[AUTO-BAN] {reason} | {command}")
        )
        conn.commit()

        # Notify user
        bot.send_message(
            user_id,
            f"🚫 <b>You have been banned</b>\n\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Command:</b> <code>{command}</code>",
            parse_mode="HTML"
        )

        # Notify admins
        for admin in admins:
            bot.send_message(
                admin,
                f"🚨 <b>AUTO-BAN ALERT</b>\n\n"
                f"User: <code>{user_id}</code>\n"
                f"Reason: {reason}\n"
                f"Command: <code>{command}</code>",
                parse_mode="HTML"
            )

    # -------------------------------------------------
    # GLOBAL FIREWALL (NO MAIN.PY TOUCH)
    # -------------------------------------------------
    @bot.message_handler(func=lambda m: True, content_types=['text'])
    def firewall(message):
        user_id = message.from_user.id
        text = message.text.strip()

     # ALLOW /verify AND /start TO PASS THROUGH
        if text.startswith(("/verify", "/start")):
            return

    # CAPTCHA ANSWER HANDLING
        if user_id in captcha_sessions:
            if text == captcha_sessions[user_id]:
                cur.execute("UPDATE users SET verified=1 WHERE user_id=?", (user_id,))
                conn.commit()
                del captcha_sessions[user_id]
                bot.reply_to(message, "✅ Verification successful")
            else:
                del captcha_sessions[user_id]
                bot.reply_to(message, "❌ Wrong CAPTCHA. Use /verify again")
            return

        # LIVE WATCH NOTIFY
        if user_id in admin_watch:
            for admin in admins:
                bot.send_message(
                    admin,
                    f"👀 <b>LIVE USER ACTIVITY</b>\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Cmd: <code>{text}</code>",
                    parse_mode="HTML"
                )

        # BLOCK UNVERIFIED USERS
        cur.execute("SELECT verified FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row or row[0] != 1:
            if not text.startswith(("/start", "/verify")):
                bot.reply_to(message, "🔒 Please verify first using /verify")
                return

        # AUTO-BAN SCAN
        lower = text.lower()
        for bad in BAD_KEYWORDS:
            if bad in lower:
                auto_ban(user_id, text, f"Detected forbidden keyword: {bad}")
                return

        if text.startswith("./"):
            auto_ban(user_id, text, "Binary execution is not allowed")
            return

        # CLEAN MESSAGE → DO NOTHING
        return
