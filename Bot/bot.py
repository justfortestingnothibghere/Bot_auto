import telebot
import google.generativeai as genai
import os
import zipfile
import shutil
import tempfile
import requests
import sqlite3
import time
from Bot.admin import register_admin_handlers  # Import admin

# Tokens and keys
BOT_TOKEN = 'your-telegram-bot-token'
GEMINI_API_KEY = 'your-gemini-api-key'
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

bot = telebot.TeleBot(BOT_TOKEN)

SERVER_URL = 'https://hostaitelegrambot.onrender.com/upload?key=teamdev'
BOT_AUTH_SECRET = 'your-super-secret-key'

# Database for premiums
DB_PATH = 'Bot/premium.db'
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS premiums (user_id INTEGER PRIMARY KEY)''')
conn.commit()

# Rate limit (simple: dict of last request time)
rate_limits = {}

def is_premium(user_id):
    cursor.execute('SELECT 1 FROM premiums WHERE user_id = ?', (user_id,))
    return cursor.fetchone() is not None

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Send /generate followed by your description. Premium users get higher limits!")

@bot.message_handler(commands=['generate'])
def generate_website(message):
    user_id = message.from_user.id
    now = time.time()
    if user_id in rate_limits and now - rate_limits[user_id] < 60:  # 1/min
        bot.reply_to(message, "Please wait 1 minute between generations.")
        return
    rate_limits[user_id] = now

    prompt = message.text.replace('/generate', '').strip()
    if not prompt:
        bot.reply_to(message, "Provide a description after /generate.")
        return

    bot.reply_to(message, "Generating...")

    # Gemini call
    ai_prompt = f"Make This Project And Etc Prompt: {prompt} Instructions"
    try:
        response = model.generate_content(ai_prompt)
        # Assume response is text; parse to dict like {'index.html': 'content', ...}
        # For simplicity, eval if it's code-like; use safer parsing in prod
        generated_files = eval(response.text)  # Adjust based on Gemini output format
    except Exception as e:
        bot.reply_to(message, f"AI error: {str(e)}")
        return

    # Create temp dir
    temp_dir = tempfile.mkdtemp()
    try:
        for filename, content in generated_files.items():
            file_path = os.path.join(temp_dir, filename)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w') as f:
                f.write(content)

        # Zip
        zip_path = os.path.join(temp_dir, 'website.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    if file != 'website.zip':
                        zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), temp_dir))

        # Check size
        zip_size = os.path.getsize(zip_path) / (1024 * 1024)  # MB
        max_size = 45 if is_premium(user_id) else 5
        if zip_size > max_size:
            bot.reply_to(message, f"Generated zip ({zip_size:.2f}MB) exceeds your limit ({max_size}MB). Upgrade to premium?")
            return

        # Upload
        with open(zip_path, 'rb') as f:
            upload_response = requests.post(
                SERVER_URL,
                headers={'X-Bot-Auth': BOT_AUTH_SECRET},
                files={'file': ('website.zip', f)}
            )
        
        if upload_response.status_code != 200:
            bot.reply_to(message, f"Upload failed: {upload_response.text}")
            return
        
        download_url = upload_response.json()['url']
        bot.reply_to(message, f"Ready! View/Download: {download_url}")
    
        # Log
        with open('Bot/logs.txt', 'a') as log:
            log.write(f"User {user_id} generated {prompt} at {time.ctime()}\n")
    
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")
    
    finally:
        shutil.rmtree(temp_dir)

# Register admin handlers
register_admin_handlers(bot)

if __name__ == '__main__':
    bot.polling()
