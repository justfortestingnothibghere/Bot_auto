import telebot
import sqlite3

DB_PATH = 'Bot/premium.db'

# Admin IDs (add yours)
ADMIN_IDS = [7618637244]  # Replace with your Telegram ID

def register_admin_handlers(bot):
    @bot.message_handler(commands=['admin_add_premium'])
    def add_premium(message):
        if message.from_user.id not in ADMIN_IDS:
            return
        try:
            user_id = int(message.text.split()[1])
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO premiums (user_id) VALUES (?)', (user_id,))
            conn.commit()
            bot.reply_to(message, f"Added {user_id} as premium.")
        except:
            bot.reply_to(message, "Usage: /admin_add_premium <user_id>")

    @bot.message_handler(commands=['admin_remove_premium'])
    def remove_premium(message):
        if message.from_user.id not in ADMIN_IDS:
            return
        try:
            user_id = int(message.text.split()[1])
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM premiums WHERE user_id = ?', (user_id,))
            conn.commit()
            bot.reply_to(message, f"Removed {user_id} from premium.")
        except:
            bot.reply_to(message, "Usage: /admin_remove_premium <user_id>")

    @bot.message_handler(commands=['admin_logs'])
    def view_logs(message):
        if message.from_user.id not in ADMIN_IDS:
            return
        try:
            with open('Bot/logs.txt', 'r') as log:
                logs = log.read()
            bot.reply_to(message, logs[-2000:])  # Last 2000 chars
        except:
            bot.reply_to(message, "No logs yet.")
