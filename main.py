import telebot
import os
import subprocess
import uuid
import datetime
import sqlite3
import threading
import time
import shutil
import psutil  # Assume psutil is installed for stats

# Replace with your Telegram Bot Token
TOKEN = '7913272382:AAGnvD29s4bu_jmsejNmT5eWbl7HZnGy_OM'

bot = telebot.TeleBot(TOKEN)

# Admin user IDs
admins = [123456789]  # Replace with actual admin user IDs

# Database setup
conn = sqlite3.connect('bot.db', check_same_thread=False)
cur = conn.cursor()

cur.execute('''CREATE TABLE IF NOT EXISTS users 
               (user_id INTEGER PRIMARY KEY, current_dir TEXT, premium_until TEXT, referral_code TEXT, referred_by INTEGER)''')
cur.execute('''CREATE TABLE IF NOT EXISTS referrals 
               (referrer INTEGER, referred INTEGER, UNIQUE(referrer, referred))''')
cur.execute('''CREATE TABLE IF NOT EXISTS logs 
               (user_id INTEGER, activity TEXT, timestamp DATETIME)''')
cur.execute('''CREATE TABLE IF NOT EXISTS bans 
               (user_id INTEGER PRIMARY KEY)''')
cur.execute('''CREATE TABLE IF NOT EXISTS processes 
               (user_id INTEGER, service_id TEXT, pid INTEGER)''')  # For persistence, but note: PIDs won't survive restarts
conn.commit()

# In-memory for active processes (since PIDs are volatile)
active_processes = {}

# States for multi-step commands like /replace
user_states = {}

def get_current_dir(user_id):
    cur.execute('SELECT current_dir FROM users WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    return result[0] if result else None

def set_current_dir(user_id, new_dir):
    cur.execute('UPDATE users SET current_dir = ? WHERE user_id = ?', (new_dir, user_id))
    conn.commit()

def is_premium(user_id):
    cur.execute('SELECT premium_until FROM users WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    if result and result[0]:
        premium_until = datetime.datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
        return premium_until > datetime.datetime.now()
    return False

def add_premium(user_id, days):
    cur.execute('SELECT premium_until FROM users WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    if result and result[0]:
        current_until = datetime.datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
    else:
        current_until = datetime.datetime.now()
    new_until = current_until + datetime.timedelta(days=days)
    cur.execute('UPDATE users SET premium_until = ? WHERE user_id = ?', (new_until.strftime('%Y-%m-%d %H:%M:%S'), user_id))
    conn.commit()

def get_referral_count(user_id):
    cur.execute('SELECT COUNT(*) FROM referrals WHERE referrer = ?', (user_id,))
    return cur.fetchone()[0]

def award_referrals(user_id):
    count = get_referral_count(user_id)
    # Simple threshold awards (can be improved with tracking awarded levels)
    if count >= 20:
        add_premium(user_id, 10)
        bot.send_message(user_id, '🎉 Congratulations! You earned 10 days of Premium for referring 20 users.')
    elif count >= 5:
        add_premium(user_id, 1)
        bot.send_message(user_id, '🎉 Congratulations! You earned 1 day of Premium for referring 5 users.')

def is_banned(user_id):
    cur.execute('SELECT 1 FROM bans WHERE user_id = ?', (user_id,))
    return cur.fetchone() is not None

def log_activity(user_id, activity):
    cur.execute('INSERT INTO logs (user_id, activity, timestamp) VALUES (?, ?, DATETIME("now"))', (user_id, activity))
    conn.commit()

@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    user_dir = f'users/{user_id}'
    if not os.path.exists(user_dir):
        os.makedirs(user_dir)
    cur.execute('INSERT OR IGNORE INTO users (user_id, current_dir) VALUES (?, ?)', (user_id, os.path.abspath(user_dir)))
    conn.commit()

    # Handle referral
    if len(message.text.split()) > 1:
        ref_code = message.text.split()[1]
        try:
            referrer_id = int(ref_code)  # Assuming referral_code is user_id for simplicity
            cur.execute('INSERT OR IGNORE INTO referrals (referrer, referred) VALUES (?, ?)', (referrer_id, user_id))
            conn.commit()
            award_referrals(referrer_id)
        except ValueError:
            pass

    # Generate referral code if not exists
    cur.execute('SELECT referral_code FROM users WHERE user_id = ?', (user_id,))
    ref_code = cur.fetchone()[0]
    if not ref_code:
        ref_code = str(user_id)
        cur.execute('UPDATE users SET referral_code = ? WHERE user_id = ?', (ref_code, user_id))
        conn.commit()

    bot.reply_to(message, f'🚀 Welcome! Your referral link: https://t.me/{bot.get_me().username}?start={ref_code}\nRefer 5 users for 1 day Premium, 20 for 10 days!')

@bot.message_handler(commands=['premium'])
def handle_premium(message):
    user_id = message.from_user.id
    if is_premium(user_id):
        cur.execute('SELECT premium_until FROM users WHERE user_id = ?', (user_id,))
        until = cur.fetchone()[0]
        bot.reply_to(message, f'⭐ You are Premium until {until}! Enjoy 2GB uploads, high speed, sudo access, and more.')
    else:
        bot.reply_to(message, '🆓 You are on Free plan (100MB uploads, low speed). Refer users to earn Premium!')

@bot.message_handler(commands=['referrals'])
def handle_referrals(message):
    user_id = message.from_user.id
    count = get_referral_count(user_id)
    bot.reply_to(message, f'📊 You have referred {count} users.')

@bot.message_handler(commands=['help'])
def handle_help(message):
    help_text = '''
📚 Commands:
- ls: List files
- cd <path>: Change directory
- git clone <url>: Clone repository
- python <script.py>: Host Python script (background)
- node <script.js>: Host Node.js script
- php <script.php>: Host PHP script
- pkg install <pkg>: Install Android pkg (termux-like)
- pip install <pkg>: Install Python pkg
- sudo <cmd>: Run with sudo (Premium only)
- stop <service_id>: Stop running service
- /stats: Show bot stats
- /delete <path>: Delete file/dir
- /replace <path>: Replace file (send file after)
- /see <path>: View file content
- /mkdir <dir>: Create directory
- /upload: Upload file (send document)
- /download <path>: Download file
- /activity: View your activity logs
- /premium: Check premium status
- /referrals: Check referral count

Admin Commands:
- /addpremium <user_id> <days>
- /removepremium <user_id>
- /ban <user_id>
- /unban <user_id>
- /warn <user_id> <reason>
- /broadcast <message>
'''
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['stats'])
def handle_stats(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    memory = psutil.virtual_memory().percent
    cpu = psutil.cpu_percent()
    bot.reply_to(message, f'📈 Bot Stats:\nCPU: {cpu}%\nMemory: {memory}%')

@bot.message_handler(commands=['delete'])
def handle_delete(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    path = ' '.join(message.text.split()[1:])
    full_path = os.path.join(get_current_dir(user_id), path)
    if os.path.exists(full_path) and full_path.startswith(f'users/{user_id}'):
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        bot.reply_to(message, '🗑️ Deleted successfully.')
    else:
        bot.reply_to(message, '❌ Path not found or invalid.')

@bot.message_handler(commands=['see'])
def handle_see(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    path = ' '.join(message.text.split()[1:])
    full_path = os.path.join(get_current_dir(user_id), path)
    if os.path.exists(full_path) and full_path.startswith(f'users/{user_id}'):
        try:
            with open(full_path, 'r') as f:
                content = f.read()
            bot.reply_to(message, f'📄 File Content:\n{content}')
        except Exception as e:
            bot.reply_to(message, f'❌ Error: {str(e)}')
    else:
        bot.reply_to(message, '❌ Path not found or invalid.')

@bot.message_handler(commands=['replace'])
def handle_replace(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    path = ' '.join(message.text.split()[1:])
    full_path = os.path.join(get_current_dir(user_id), path)
    if os.path.exists(full_path) and full_path.startswith(f'users/{user_id}'):
        user_states[user_id] = {'action': 'replace', 'path': full_path}
        bot.reply_to(message, '📤 Send the new file to replace.')
    else:
        bot.reply_to(message, '❌ Path not found or invalid.')

@bot.message_handler(commands=['mkdir'])
def handle_mkdir(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    dir_name = ' '.join(message.text.split()[1:])
    full_path = os.path.join(get_current_dir(user_id), dir_name)
    if full_path.startswith(f'users/{user_id}'):
        os.makedirs(full_path, exist_ok=True)
        bot.reply_to(message, '📁 Directory created.')
    else:
        bot.reply_to(message, '❌ Invalid path.')

@bot.message_handler(commands=['download'])
def handle_download(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    path = ' '.join(message.text.split()[1:])
    full_path = os.path.join(get_current_dir(user_id), path)
    if os.path.exists(full_path) and os.path.isfile(full_path) and full_path.startswith(f'users/{user_id}'):
        with open(full_path, 'rb') as f:
            bot.send_document(message.chat.id, f)
    else:
        bot.reply_to(message, '❌ File not found or invalid.')

@bot.message_handler(commands=['activity'])
def handle_activity(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    cur.execute('SELECT activity, timestamp FROM logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20', (user_id,))
    logs = cur.fetchall()
    log_text = '\n'.join([f'{ts}: {act}' for act, ts in logs])
    bot.reply_to(message, f'📝 Your Recent Activity:\n{log_text}' or 'No activity yet.')

# Admin commands
@bot.message_handler(commands=['addpremium'])
def handle_addpremium(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    parts = message.text.split()[1:]
    if len(parts) == 2:
        target_id, days = int(parts[0]), int(parts[1])
        add_premium(target_id, days)
        bot.reply_to(message, '⭐ Premium added.')
        bot.send_message(target_id, f'⭐ You have been granted {days} days of Premium!')

@bot.message_handler(commands=['removepremium'])
def handle_removepremium(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    target_id = int(message.text.split()[1])
    cur.execute('UPDATE users SET premium_until = NULL WHERE user_id = ?', (target_id,))
    conn.commit()
    bot.reply_to(message, '🆓 Premium removed.')
    bot.send_message(target_id, '🆓 Your Premium has been removed.')

@bot.message_handler(commands=['ban'])
def handle_ban(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    target_id = int(message.text.split()[1])
    cur.execute('INSERT OR IGNORE INTO bans (user_id) VALUES (?)', (target_id,))
    conn.commit()
    bot.reply_to(message, '🚫 User banned.')
    bot.send_message(target_id, '🚫 You have been banned.')

@bot.message_handler(commands=['unban'])
def handle_unban(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    target_id = int(message.text.split()[1])
    cur.execute('DELETE FROM bans WHERE user_id = ?', (target_id,))
    conn.commit()
    bot.reply_to(message, '✅ User unbanned.')
    bot.send_message(target_id, '✅ You have been unbanned.')

@bot.message_handler(commands=['warn'])
def handle_warn(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    parts = message.text.split()[1:]
    target_id = int(parts[0])
    reason = ' '.join(parts[1:])
    bot.send_message(target_id, f'⚠️ Warning: {reason}')
    bot.reply_to(message, '⚠️ Warning sent.')

@bot.message_handler(commands=['broadcast'])
def handle_broadcast(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    broadcast_msg = ' '.join(message.text.split()[1:])
    cur.execute('SELECT user_id FROM users')
    users = cur.fetchall()
    for u in users:
        try:
            bot.send_message(u[0], f'📢 Broadcast: {broadcast_msg}')
        except:
            pass
    bot.reply_to(message, '📢 Broadcast sent.')

# Handle file uploads
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return

    file_id = message.document.file_id
    file_info = bot.get_file(file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    file_size = len(downloaded_file)

    limit = 2 * 1024 * 1024 * 1024 if is_premium(user_id) else 100 * 1024 * 1024
    if file_size > limit:
        bot.reply_to(message, '❌ File exceeds limit.')
        return

    file_name = message.document.file_name
    full_path = os.path.join(get_current_dir(user_id), file_name)
    with open(full_path, 'wb') as new_file:
        new_file.write(downloaded_file)

    bot.reply_to(message, '📥 File uploaded successfully.')

    # Handle states like replace
    if user_id in user_states and user_states[user_id]['action'] == 'replace':
        replace_path = user_states[user_id]['path']
        os.remove(replace_path)
        shutil.move(full_path, replace_path)
        bot.reply_to(message, '🔄 File replaced successfully.')
        del user_states[user_id]

# Main handler for shell-like commands
@bot.message_handler(func=lambda m: True)
def handle_shell(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, '🚫 You are banned.')
        return

    text = message.text.strip()
    log_activity(user_id, text)

    current_dir = get_current_dir(user_id)
    if not current_dir:
        return  # User not initialized

    os.chdir(current_dir)

    # Real-time progress animation helper
    def animate_progress(msg_id, done_event):
        dots = ''
        while not done_event.is_set():
            dots = (dots + '.') if len(dots) < 3 else ''
            try:
                bot.edit_message_text(f'⏳ Running{dots}', message.chat.id, msg_id)
            except:
                pass
            time.sleep(1)

    if text == 'ls':
        files = os.listdir('.')
        bot.reply_to(message, '📂 Files:\n' + '\n'.join(files))
    elif text.startswith('cd '):
        path = text[3:].strip()
        new_dir = os.path.abspath(path)
        if os.path.isdir(new_dir) and new_dir.startswith(os.path.abspath(f'users/{user_id}')):
            set_current_dir(user_id, new_dir)
            bot.reply_to(message, '📂 Directory changed.')
        else:
            bot.reply_to(message, '❌ Invalid directory.')
    elif text.startswith('git clone '):
        cmd = text
        msg = bot.reply_to(message, '⏳ Running')
        done_event = threading.Event()
        animator = threading.Thread(target=animate_progress, args=(msg.message_id, done_event))
        animator.start()
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=300)
            done_event.set()
            animator.join()
            bot.edit_message_text('✅ Done:\n' + output.decode(), message.chat.id, msg.message_id)
        except Exception as e:
            done_event.set()
            animator.join()
            bot.edit_message_text(f'❌ Error: {str(e)}', message.chat.id, msg.message_id)
    elif text.startswith(('pkg install ', 'pip install ')) or (text.startswith('sudo ') and is_premium(user_id)):
        if text.startswith('sudo ') and not is_premium(user_id):
            bot.reply_to(message, '⭐ Premium feature only.')
            return
        cmd = text
        msg = bot.reply_to(message, '⏳ Running')
        done_event = threading.Event()
        animator = threading.Thread(target=animate_progress, args=(msg.message_id, done_event))
        animator.start()
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=300)
            done_event.set()
            animator.join()
            bot.edit_message_text('✅ Done:\n' + output.decode(), message.chat.id, msg.message_id)
        except Exception as e:
            done_event.set()
            animator.join()
            bot.edit_message_text(f'❌ Error: {str(e)}', message.chat.id, msg.message_id)
    elif text.startswith(('python ', 'node ', 'php ')):
        cmd = text
        service_id = str(uuid.uuid4())
        msg = bot.reply_to(message, f'🚀 Starting service {service_id}...')
        try:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if user_id not in active_processes:
                active_processes[user_id] = {}
            active_processes[user_id][service_id] = proc
            # Persist to DB (optional, for listing)
            cur.execute('INSERT INTO processes (user_id, service_id, pid) VALUES (?, ?, ?)', (user_id, service_id, proc.pid))
            conn.commit()
            bot.edit_message_text(f'✅ Service {service_id} started. PID: {proc.pid}', message.chat.id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f'❌ Error: {str(e)}', message.chat.id, msg.message_id)
    elif text.startswith('stop '):
        service_id = text[5:].strip()
        if user_id in active_processes and service_id in active_processes[user_id]:
            proc = active_processes[user_id][service_id]
            proc.kill()
            del active_processes[user_id][service_id]
            cur.execute('DELETE FROM processes WHERE service_id = ?', (service_id,))
            conn.commit()
            bot.reply_to(message, f'🛑 Service {service_id} stopped.')
        else:
            bot.reply_to(message, '❌ Service not found.')
    else:
        bot.reply_to(message, '❓ Unknown command. Use /help for list.')

# Polling
bot.infinity_polling()