import telebot
from telebot import types
import os
import subprocess
import uuid
import datetime
import sqlite3
import threading
import time
import shutil
import psutil
import zipfile
import tarfile
from collections import deque

# Replace with your Telegram Bot Token
TOKEN = '8563927642:AAEWemxZFF8iySCVGoiHuSSb0KhDuKy_5A4'

bot = telebot.TeleBot(TOKEN, parse_mode='HTML')

# Admin user IDs
admins = [8163739723]  # Replace with actual admin user IDs

# Database setup
conn = sqlite3.connect('bot.db', check_same_thread=False)
cur = conn.cursor()

cur.execute('''CREATE TABLE IF NOT EXISTS users 
               (user_id INTEGER PRIMARY KEY, current_dir TEXT, premium_until TEXT, 
                referral_code TEXT, referred_by INTEGER, disk_quota INTEGER DEFAULT 1073741824)''')
cur.execute('''CREATE TABLE IF NOT EXISTS referrals 
               (referrer INTEGER, referred INTEGER, UNIQUE(referrer, referred))''')
cur.execute('''CREATE TABLE IF NOT EXISTS logs 
               (user_id INTEGER, activity TEXT, timestamp DATETIME)''')
cur.execute('''CREATE TABLE IF NOT EXISTS bans 
               (user_id INTEGER PRIMARY KEY)''')
cur.execute('''CREATE TABLE IF NOT EXISTS processes 
               (user_id INTEGER, service_id TEXT, pid INTEGER, command TEXT, started_at DATETIME)''')
conn.commit()

# In-memory for active processes and log streaming
active_processes = {}
service_logs = {}  # {service_id: deque of log lines}
log_streaming = {}  # {service_id: {chat_id, message_id}}

# States for multi-step commands
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
    cur.execute('UPDATE users SET premium_until = ? WHERE user_id = ?', 
                (new_until.strftime('%Y-%m-%d %H:%M:%S'), user_id))
    conn.commit()

def get_referral_count(user_id):
    cur.execute('SELECT COUNT(*) FROM referrals WHERE referrer = ?', (user_id,))
    return cur.fetchone()[0]

def award_referrals(user_id):
    count = get_referral_count(user_id)
    if count >= 20:
        add_premium(user_id, 10)
        bot.send_message(user_id, '🎉 <b>Congratulations!</b> You earned 10 days of Premium for referring 20 users.')
    elif count >= 5:
        add_premium(user_id, 1)
        bot.send_message(user_id, '🎉 <b>Congratulations!</b> You earned 1 day of Premium for referring 5 users.')

def is_banned(user_id):
    cur.execute('SELECT 1 FROM bans WHERE user_id = ?', (user_id,))
    return cur.fetchone() is not None

def log_activity(user_id, activity):
    cur.execute('INSERT INTO logs (user_id, activity, timestamp) VALUES (?, ?, DATETIME("now"))', 
                (user_id, activity))
    conn.commit()

def get_dir_size(path):
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += get_dir_size(entry.path)
    except:
        pass
    return total

def format_bytes(bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes < 1024.0:
            return f"{bytes:.2f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.2f} PB"

def get_user_quota(user_id):
    cur.execute('SELECT disk_quota FROM users WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    return result[0] if result else (1 * 1024 * 1024 * 1024)  # Default 1GB
  
@bot.message_handler(commands=['start'])
def handle_start(message):
    user = message.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = f"@{user.username}" if user.username else ""

    # --- STEP 1: Send processing message ---
    loading_msg = bot.send_message(
        message.chat.id,
        "⏳ <b>Please wait...</b>\nProcessing...",
        parse_mode="HTML"
    )

    # --- STEP 2: Loading animation (edit message) ---
    animation = ["⏳ Processing.", "⏳ Processing..", "⏳ Processing..."]
    for frame in animation:
        bot.edit_message_text(
            frame,
            chat_id=message.chat.id,
            message_id=loading_msg.message_id,
            parse_mode="HTML"
        )
        time.sleep(0.5)

    # --- STEP 3: Create user directory ---
    user_dir = f'users/{user_id}'
    if not os.path.exists(user_dir):
        os.makedirs(user_dir)

    cur.execute(
        'INSERT OR IGNORE INTO users (user_id, current_dir) VALUES (?, ?)',
        (user_id, os.path.abspath(user_dir))
    )
    conn.commit()

    # --- STEP 4: Handle referral ---
    if len(message.text.split()) > 1:
        ref_code = message.text.split()[1]
        try:
            referrer_id = int(ref_code)
            if referrer_id != user_id:
                cur.execute(
                    'INSERT OR IGNORE INTO referrals (referrer, referred) VALUES (?, ?)',
                    (referrer_id, user_id)
                )
                conn.commit()
                award_referrals(referrer_id)
        except ValueError:
            pass

    # --- STEP 5: Generate referral code ---
    cur.execute('SELECT referral_code FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()

    if row and row[0]:
        ref_code = row[0]
    else:
        ref_code = str(user_id)
        cur.execute(
            'UPDATE users SET referral_code = ? WHERE user_id = ?',
            (ref_code, user_id)
        )
        conn.commit()

    # --- STEP 6: Final caption message ---
    caption = f"""
<b>Hey</b> {first_name} {username} 🚀
<i>• Thanks For Joining Me.</i>

🤖 <b>Introduce Bot</b>
This bot helps you manage advanced shell features easily.

🔗 <b>Your Refer Code:</b>
<code>{ref_code}</code>

📎 <b>Referral Link:</b>
<code>https://t.me/{bot.get_me().username}?start={ref_code}</code>
"""

    # --- STEP 7: Delete loading message ---
    bot.delete_message(message.chat.id, loading_msg.message_id)

    # --- STEP 8: Send image with caption ---
    with open("images/start.jpg", "rb") as photo:
        bot.send_photo(
            message.chat.id,
            photo,
            caption=caption,
            parse_mode="HTML"
        )
      
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

ADMIN_URL = "https://t.me/mr_arman_08"
GIF_PATH = "images/premium.gif"   # animated gif

@bot.message_handler(commands=['premium'])
def handle_premium(message):
    user_id = message.from_user.id

    # Loading animation message
    loading = bot.reply_to(
        message,
        "⚡ <b>Initializing Premium Interface</b>\n\n▰▱▱▱▱▱▱▱▱▱",
        parse_mode="HTML"
    )

    frames = [
        "▰▱▱▱▱▱▱▱▱▱",
        "▰▰▱▱▱▱▱▱▱▱",
        "▰▰▰▱▱▱▱▱▱▱",
        "▰▰▰▰▱▱▱▱▱▱",
        "▰▰▰▰▰▱▱▱▱▱",
        "▰▰▰▰▰▰▱▱▱▱",
        "▰▰▰▰▰▰▰▱▱▱",
        "▰▰▰▰▰▰▰▰▱▱",
        "▰▰▰▰▰▰▰▰▰▱",
        "▰▰▰▰▰▰▰▰▰▰"
    ]

    for bar in frames:
        time.sleep(0.12)
        bot.edit_message_text(
            f"⚡ <b>Initializing Premium Interface</b>\n\n{bar}",
            message.chat.id,
            loading.message_id,
            parse_mode="HTML"
        )

    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("💬 Contact Admin", url=ADMIN_URL)
    )

    if is_premium(user_id):
        cur.execute(
            "SELECT premium_until FROM users WHERE user_id = ?",
            (user_id,)
        )
        until = cur.fetchone()[0]

        caption = f"""
🚀 <b>PREMIUM STATUS: ACTIVE</b>
━━━━━━━━━━━━━━━━━━━
🟢 <b>Access Level:</b> ELITE
⏳ <b>Valid Until:</b> <code>{until}</code>

⚡ <b>Unlocked Capabilities</b>
━━━━━━━━━━━━━━━━━━━
📦 2GB Upload Limit
🚄 Ultra-Fast Processing
🛡 Sudo / Admin Access
🎧 Priority Support
🧠 Advanced Features

✨ <i>System running at maximum power.</i>
"""
    else:
        caption = """
🆓 <b>FREE ACCESS MODE</b>
━━━━━━━━━━━━━━━━━━━
🟡 <b>Access Level:</b> BASIC

⚠️ <b>Current Limits</b>
━━━━━━━━━━━━━━━━━━━
📦 100MB Upload Limit
🐢 Normal Speed
🔒 No Sudo Access

🚀 <b>Upgrade to Premium</b>
━━━━━━━━━━━━━━━━━━━
Unlock elite power now 👇
"""

        keyboard.add(
            InlineKeyboardButton("🛒 Buy Premium", url=ADMIN_URL)
        )

    # Remove loading message
    bot.delete_message(message.chat.id, loading.message_id)

    # Send animated GIF with caption
    with open(GIF_PATH, "rb") as gif:
        bot.send_animation(
            message.chat.id,
            gif,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
  )
      
@bot.message_handler(commands=['help'])
def handle_help(message):
    help_text = '''
📚 <b>Available Commands</b>

<b>📁 File Management:</b>
• <code>ls</code> - List files
• <code>cd &lt;path&gt;</code> - Change directory
• <code>/mkdir &lt;dir&gt;</code> - Create directory
• <code>/delete &lt;path&gt;</code> - Delete file/dir
• <code>/see &lt;path&gt;</code> - View file content
• <code>/replace &lt;path&gt;</code> - Replace file
• <code>/upload</code> - Upload file (send document)
• <code>/download &lt;path&gt;</code> - Download file/folder
• <code>/zip &lt;name&gt; &lt;paths...&gt;</code> - Create zip archive
• <code>/tree</code> - Show directory tree

<b>⚙️ Process Management:</b>
• <code>python &lt;script.py&gt;</code> - Run Python script
• <code>node &lt;script.js&gt;</code> - Run Node.js script
• <code>php &lt;script.php&gt;</code> - Run PHP script
• <code>/ps</code> - List your processes
• <code>/log &lt;service_id&gt;</code> - Live log streaming
• <code>stop &lt;service_id&gt;</code> - Stop service
• <code>/killall</code> - Kill all processes (admin)

<b>📊 System Info:</b>
• <code>/stats</code> - Bot statistics
• <code>/top</code> - Your resource usage
• <code>/disk</code> - Disk usage

<b>🔧 Package Management:</b>
• <code>pkg install &lt;pkg&gt;</code> - Install package
• <code>pip install &lt;pkg&gt;</code> - Install Python package
• <code>git clone &lt;url&gt;</code> - Clone repository

<b>👤 Account:</b>
• <code>/premium</code> - Check premium status
• <code>/referrals</code> - Referral stats
• <code>/activity</code> - Activity logs

<b>⭐ Premium Only:</b>
• <code>sudo &lt;cmd&gt;</code> - Run with elevated privileges
'''
    
    if message.from_user.id in admins:
        help_text += '''

<b>👑 Admin Commands:</b>
• <code>/addpremium &lt;user_id&gt; &lt;days&gt;</code>
• <code>/removepremium &lt;user_id&gt;</code>
• <code>/ban &lt;user_id&gt;</code>
• <code>/unban &lt;user_id&gt;</code>
• <code>/warn &lt;user_id&gt; &lt;reason&gt;</code>
• <code>/broadcast &lt;message&gt;</code>
• <code>/killall</code> - Kill all processes
• <code>/userinfo &lt;user_id&gt;</code>
'''
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['stats'])
def handle_stats(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    
    memory = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=1)
    disk = psutil.disk_usage('/')
    
    cur.execute('SELECT COUNT(*) FROM users')
    total_users = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM processes')
    total_processes = cur.fetchone()[0]
    
    msg = f'''
📈 <b>Bot Statistics</b>

<b>System Resources:</b>
• CPU: {cpu}%
• Memory: {memory.percent}% ({format_bytes(memory.used)}/{format_bytes(memory.total)})
• Disk: {disk.percent}% ({format_bytes(disk.used)}/{format_bytes(disk.total)})

<b>Bot Stats:</b>
• Total Users: {total_users}
• Active Processes: {total_processes}
• Uptime: {format_bytes(psutil.boot_time())}
'''
    bot.reply_to(message, msg)

@bot.message_handler(commands=['top'])
def handle_top(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    
    user_dir = get_current_dir(user_id)
    dir_size = get_dir_size(user_dir)
    quota = get_user_quota(user_id)
    
    # Count user processes
    user_procs = active_processes.get(user_id, {})
    proc_count = len(user_procs)
    
    # Calculate CPU/Memory for user processes
    cpu_total = 0
    mem_total = 0
    proc_info = []
    
    for service_id, proc in user_procs.items():
        try:
            p = psutil.Process(proc.pid)
            cpu_total += p.cpu_percent(interval=0.1)
            mem_total += p.memory_info().rss
            proc_info.append(f"  • {service_id[:8]}: CPU {p.cpu_percent():.1f}%, MEM {format_bytes(p.memory_info().rss)}")
        except:
            pass
    
    msg = f'''
📊 <b>Your Resource Usage</b>

<b>Disk Storage:</b>
• Used: {format_bytes(dir_size)} / {format_bytes(quota)}
• Available: {format_bytes(quota - dir_size)}
• Usage: {(dir_size/quota*100):.1f}%

<b>Processes:</b>
• Active: {proc_count}
• Total CPU: {cpu_total:.1f}%
• Total Memory: {format_bytes(mem_total)}

<b>Process Details:</b>
{chr(10).join(proc_info) if proc_info else '  No active processes'}
'''
    bot.reply_to(message, msg)

@bot.message_handler(commands=['disk'])
def handle_disk(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    
    user_dir = get_current_dir(user_id)
    dir_size = get_dir_size(user_dir)
    quota = get_user_quota(user_id)
    
    # Get breakdown by subdirectories
    subdirs = []
    try:
        for entry in os.scandir(user_dir):
            if entry.is_dir(follow_symlinks=False):
                size = get_dir_size(entry.path)
                subdirs.append((entry.name, size))
    except:
        pass
    
    subdirs.sort(key=lambda x: x[1], reverse=True)
    subdir_text = '\n'.join([f"  • {name}: {format_bytes(size)}" for name, size in subdirs[:10]])
    
    msg = f'''
💾 <b>Disk Usage Analysis</b>

<b>Total Usage:</b>
{format_bytes(dir_size)} / {format_bytes(quota)} ({(dir_size/quota*100):.1f}%)

<b>Top Directories:</b>
{subdir_text or '  No subdirectories'}

<b>Quota:</b> {'Premium (2GB)' if is_premium(user_id) else 'Free (1GB)'}
'''
    bot.reply_to(message, msg)

@bot.message_handler(commands=['ps'])
def handle_ps(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    
    user_procs = active_processes.get(user_id, {})
    
    if not user_procs:
        bot.reply_to(message, '📋 No active processes')
        return
    
    msg = '<b>🔄 Your Active Processes</b>\n\n'
    
    for service_id, proc in user_procs.items():
        try:
            p = psutil.Process(proc.pid)
            cur.execute('SELECT command, started_at FROM processes WHERE service_id = ?', (service_id,))
            result = cur.fetchone()
            cmd = result[0] if result else 'Unknown'
            started = result[1] if result else 'Unknown'
            
            msg += f'''
<b>Service:</b> <code>{service_id}</code>
<b>Command:</b> <code>{cmd[:50]}</code>
<b>PID:</b> {proc.pid}
<b>Status:</b> {p.status()}
<b>Started:</b> {started}
<b>CPU:</b> {p.cpu_percent():.1f}%
<b>Memory:</b> {format_bytes(p.memory_info().rss)}
────────────────
'''
        except:
            msg += f'<b>Service:</b> <code>{service_id}</code> (Not running)\n────────────────\n'
    
    bot.reply_to(message, msg)

@bot.message_handler(commands=['log'])
def handle_log(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, '❌ Usage: /log &lt;service_id&gt;')
        return
    
    service_id = parts[1]
    
    if user_id not in active_processes or service_id not in active_processes[user_id]:
        bot.reply_to(message, '❌ Service not found')
        return
    
    # Start log streaming
    msg = bot.reply_to(message, f'📜 <b>Live Logs for {service_id}</b>\n\n<code>Starting...</code>')
    log_streaming[service_id] = {'chat_id': message.chat.id, 'message_id': msg.message_id}
    
    # Start log collector thread if not exists
    if service_id not in service_logs:
        service_logs[service_id] = deque(maxlen=50)
        threading.Thread(target=stream_logs, args=(user_id, service_id), daemon=True).start()

def stream_logs(user_id, service_id):
    """Stream logs from process stdout/stderr with non-blocking periodic updates"""
    proc = active_processes[user_id][service_id]
    service_logs[service_id] = deque(maxlen=100)  # Increased buffer

    last_update = time.time()
    update_interval = 2  # Update every 2 seconds even if no new output

    while service_id in log_streaming and proc.poll() is None:
        try:
            # Non-blocking read with timeout
            line = proc.stdout.readline()
            if line:
                decoded = line.decode('utf-8', errors='replace').rstrip()
                if decoded:
                    service_logs[service_id].append(decoded)
                last_update = time.time()  # Reset timer on new output

            # Force update every few seconds even if no output
            if time.time() - last_update >= update_interval:
                logs = list(service_logs[service_id])
                log_text = '\n'.join(logs[-30:]) if logs else 'No output yet...'
                
                stream_info = log_streaming.get(service_id)
                if stream_info:
                    try:
                        bot.edit_message_text(
                            f'📜 <b>Live Logs for {service_id}</b>\n\n'
                            f'<code>{log_text}</code>\n\n'
                            f'🔄 Last updated: {datetime.datetime.now().strftime("%H:%M:%S")}',
                            stream_info['chat_id'],
                            stream_info['message_id'],
                            parse_mode='HTML'
                        )
                    except Exception as e:
                        # If message too long or other error, stop streaming
                        if "message is not modified" not in str(e).lower():
                            pass
                last_update = time.time()

            time.sleep(0.5)  # Small sleep to avoid CPU hogging

        except Exception as e:
            break

    # Process finished — send final logs
    final_logs = list(service_logs[service_id])
    if proc.stdout:
        remaining = proc.stdout.read()
        if remaining:
            for line in remaining.decode('utf-8', errors='replace').splitlines():
                if line.strip():
                    final_logs.append(line.strip())
                    if len(final_logs) > 100:
                        final_logs.pop(0)

    final_text = '\n'.join(final_logs[-30:]) if final_logs else 'No output.'
    status = '✅ Completed' if proc.returncode == 0 else f'❌ Exit code: {proc.returncode}'

    stream_info = log_streaming.get(service_id)
    if stream_info:
        try:
            bot.edit_message_text(
                f'📜 <b>Logs for {service_id}</b> ({status})\n\n'
                f'<code>{final_text}</code>',
                stream_info['chat_id'],
                stream_info['message_id'],
                parse_mode='HTML'
            )
        except:
            pass

    # Cleanup
    if service_id in log_streaming:
        del log_streaming[service_id]
    if service_id in service_logs:
        del service_logs[service_id]

@bot.message_handler(commands=['killall'])
def handle_killall(message):
    user_id = message.from_user.id
    if user_id not in admins:
        bot.reply_to(message, '❌ Admin only command')
        return
    
    killed = 0
    for uid in list(active_processes.keys()):
        for service_id in list(active_processes[uid].keys()):
            try:
                active_processes[uid][service_id].kill()
                killed += 1
            except:
                pass
        active_processes[uid].clear()
    
    cur.execute('DELETE FROM processes')
    conn.commit()
    
    bot.reply_to(message, f'🛑 <b>Killed {killed} processes</b>')

@bot.message_handler(commands=['tree'])
def handle_tree(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    
    current_dir = get_current_dir(user_id)
    
    def build_tree(path, prefix='', is_last=True):
        tree = ''
        try:
            entries = sorted(os.listdir(path))
            for i, entry in enumerate(entries):
                is_last_entry = i == len(entries) - 1
                full_path = os.path.join(path, entry)
                
                connector = '└── ' if is_last_entry else '├── '
                tree += f'{prefix}{connector}{entry}\n'
                
                if os.path.isdir(full_path):
                    extension = '    ' if is_last_entry else '│   '
                    tree += build_tree(full_path, prefix + extension, is_last_entry)
        except:
            pass
        return tree
    
    tree_output = f'📁 <b>Directory Tree</b>\n\n<code>.\n{build_tree(current_dir)}</code>'
    bot.reply_to(message, tree_output[:4000])  # Telegram message limit

@bot.message_handler(commands=['zip'])
def handle_zip(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    
    parts = message.text.split()[1:]
    if len(parts) < 2:
        bot.reply_to(message, '❌ Usage: /zip &lt;archive_name&gt; &lt;files...&gt;')
        return
    
    archive_name = parts[0]
    if not archive_name.endswith('.zip'):
        archive_name += '.zip'
    
    current_dir = get_current_dir(user_id)
    archive_path = os.path.join(current_dir, archive_name)
    
    msg = bot.reply_to(message, '🗜️ Creating archive...')
    
    try:
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for item in parts[1:]:
                item_path = os.path.join(current_dir, item)
                if os.path.exists(item_path):
                    if os.path.isfile(item_path):
                        zipf.write(item_path, item)
                    else:
                        for root, dirs, files in os.walk(item_path):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, current_dir)
                                zipf.write(file_path, arcname)
        
        bot.edit_message_text(f'✅ Archive created: <code>{archive_name}</code>', message.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f'❌ Error: {str(e)}', message.chat.id, msg.message_id)

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
        bot.reply_to(message, '🗑️ <b>Deleted successfully</b>')
    else:
        bot.reply_to(message, '❌ Path not found or invalid')

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
                content = f.read()[:4000]  # Limit for Telegram
            bot.reply_to(message, f'📄 <b>File Content:</b>\n\n<code>{content}</code>')
        except Exception as e:
            bot.reply_to(message, f'❌ Error: {str(e)}')
    else:
        bot.reply_to(message, '❌ Path not found or invalid')
        
        
@bot.message_handler(commands=['download'])
def handle_download(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    path = ' '.join(message.text.split()[1:])
    full_path = os.path.join(get_current_dir(user_id), path)
    
    if not os.path.exists(full_path) or not full_path.startswith(f'users/{user_id}'):
        bot.reply_to(message, '❌ Path not found or invalid')
        return
    
    if os.path.isfile(full_path):
        with open(full_path, 'rb') as f:
            bot.send_document(message.chat.id, f)
    elif os.path.isdir(full_path):
        # Zip the directory first
        msg = bot.reply_to(message, '🗜️ Compressing folder...')
        zip_path = full_path + '.zip'
        try:
            shutil.make_archive(full_path, 'zip', full_path)
            with open(zip_path, 'rb') as f:
                bot.send_document(message.chat.id, f)
            os.remove(zip_path)
            bot.delete_message(message.chat.id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f'❌ Error: {str(e)}', message.chat.id, msg.message_id)

@bot.message_handler(commands=['activity'])
def handle_activity(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    cur.execute('SELECT activity, timestamp FROM logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20', 
                (user_id,))
    logs = cur.fetchall()
    log_text = '\n'.join([f'• {ts}: <code>{act[:50]}</code>' for act, ts in logs])
    bot.reply_to(message, f'📝 <b>Your Recent Activity</b>\n\n{log_text}' or 'No activity yet')

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
        bot.reply_to(message, f'⭐ Premium added to user {target_id}')
        bot.send_message(target_id, f'⭐ You have been granted <b>{days} days</b> of Premium!')

@bot.message_handler(commands=['removepremium'])
def handle_removepremium(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    target_id = int(message.text.split()[1])
    cur.execute('UPDATE users SET premium_until = NULL WHERE user_id = ?', (target_id,))
    conn.commit()
    bot.reply_to(message, '🆓 Premium removed')
    bot.send_message(target_id, '🆓 Your Premium has been removed')

@bot.message_handler(commands=['ban'])
def handle_ban(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    target_id = int(message.text.split()[1])
    cur.execute('INSERT OR IGNORE INTO bans (user_id) VALUES (?)', (target_id,))
    conn.commit()
    bot.reply_to(message, f'🚫 User {target_id} banned')
    bot.send_message(target_id, '🚫 You have been banned from using this bot')

@bot.message_handler(commands=['unban'])
def handle_unban(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    target_id = int(message.text.split()[1])
    cur.execute('DELETE FROM bans WHERE user_id = ?', (target_id,))
    conn.commit()
    bot.reply_to(message, f'✅ User {target_id} unbanned')
    bot.send_message(target_id, '✅ You have been unbanned')

@bot.message_handler(commands=['warn'])
def handle_warn(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    parts = message.text.split()[1:]
    if len(parts) < 2:
        return
    target_id = int(parts[0])
    reason = ' '.join(parts[1:])
    bot.send_message(target_id, f'⚠️ <b>Warning:</b> {reason}')
    bot.reply_to(message, '⚠️ Warning sent')

@bot.message_handler(commands=['broadcast'])
def handle_broadcast(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    broadcast_msg = ' '.join(message.text.split()[1:])
    cur.execute('SELECT user_id FROM users')
    users = cur.fetchall()
    success = 0
    for u in users:
        try:
            bot.send_message(u[0], f'📢 <b>Broadcast:</b>\n\n{broadcast_msg}')
            success += 1
        except:
            pass
    bot.reply_to(message, f'📢 Broadcast sent to {success} users')
    


@bot.message_handler(commands=['mkdir'])
def handle_mkdir(message):
    user_id = message.from_user.id
    if is_banned(user_id): return
    try:
        path = message.text.split(maxsplit=1)[1]
        full_path = os.path.join(get_current_dir(user_id), path)
        if full_path.startswith(f'users/{user_id}'):
            os.makedirs(full_path, exist_ok=True)
            bot.reply_to(message, f"📁 Directory created: <code>{path}</code>")
        else:
            bot.reply_to(message, "❌ Access denied")
    except:
        bot.reply_to(message, "❌ Usage: /mkdir <directory_name>")
        
        
@bot.message_handler(commands=['upload'])
def handle_upload(message):
    bot.reply_to(message, "📤 Send me the file you want to upload to your current directory.")

@bot.message_handler(commands=['replace'])
def handle_replace(message):
    try:
        path = message.text.split(maxsplit=1)[1]
        full_path = os.path.join(get_current_dir(message.from_user.id), path)
        if os.path.exists(full_path):
            user_states[message.from_user.id] = {'action': 'replace', 'path': full_path}
            bot.reply_to(message, f"🔄 Send the new file to replace:\n<code>{path}</code>")
        else:
            bot.reply_to(message, "❌ File not found")
    except:
        bot.reply_to(message, "❌ Usage: /replace <path>")
        



@bot.message_handler(commands=['userinfo'])
def handle_userinfo(message):
    user_id = message.from_user.id
    if user_id not in admins:
        return
    target_id = int(message.text.split()[1])
    
    cur.execute('SELECT * FROM users WHERE user_id = ?', (target_id,))
    user_data = cur.fetchone()
    
    if not user_data:
        bot.reply_to(message, '❌ User not found')
        return
    
    user_procs = active_processes.get(target_id, {})
    ref_count = get_referral_count(target_id)
    user_dir = get_current_dir(target_id)
    dir_size = get_dir_size(user_dir) if user_dir else 0
    
    msg = f'''
👤 <b>User Information</b>

<b>User ID:</b> <code>{target_id}</code>
<b>Premium:</b> {'Yes (' + str(user_data[2]) + ')' if user_data[2] else 'No'}
<b>Referrals:</b> {ref_count}
<b>Disk Usage:</b> {format_bytes(dir_size)}
<b>Active Processes:</b> {len(user_procs)}
<b>Banned:</b> {'Yes' if is_banned(target_id) else 'No'}
'''
    bot.reply_to(message, msg)

# Handle file uploads
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, '🚫 You are banned')
        return

    file_id = message.document.file_id
    file_info = bot.get_file(file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    file_size = len(downloaded_file)

    # Check quota
    user_dir = get_current_dir(user_id)
    current_usage = get_dir_size(user_dir)
    quota = get_user_quota(user_id)
    
    if current_usage + file_size > quota:
        bot.reply_to(message, f'❌ Quota exceeded. You have {format_bytes(quota - current_usage)} remaining')
        return

    # Check upload limit
    limit = 2 * 1024 * 1024 * 1024 if is_premium(user_id) else 100 * 1024 * 1024
    if file_size > limit:
        bot.reply_to(message, f'❌ File exceeds limit ({format_bytes(limit)})')
        return

    file_name = message.document.file_name
    full_path = os.path.join(get_current_dir(user_id), file_name)
    
    msg = bot.reply_to(message, '📥 Uploading...')
    
    with open(full_path, 'wb') as new_file:
        new_file.write(downloaded_file)

    bot.edit_message_text(f'✅ <b>File uploaded:</b> <code>{file_name}</code>\n<b>Size:</b> {format_bytes(file_size)}', 
                         message.chat.id, msg.message_id)

    # Handle states like replace
    if user_id in user_states and user_states[user_id]['action'] == 'replace':
        replace_path = user_states[user_id]['path']
        os.remove(replace_path)
        shutil.move(full_path, replace_path)
        bot.send_message(message.chat.id, '🔄 <b>File replaced successfully</b>')
        del user_states[user_id]

# Main handler for shell-like commands
@bot.message_handler(func=lambda m: True)
def handle_shell(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, '🚫 You are banned')
        return

    text = message.text.strip()
    log_activity(user_id, text)

    current_dir = get_current_dir(user_id)
    if not current_dir:
        return
        
    if text.startswith('/'):
        return  # Let the specific @bot.message_handler(commands=...) handle it

    # Now safely process only shell-like commands (no /)
    log_activity(user_id, text)
    
    
    os.chdir(current_dir)

    # Real-time progress animation helper
    def animate_progress(msg_id, done_event):
        dots = ''
        while not done_event.is_set():
            dots = (dots + '.') if len(dots) < 3 else ''
            try:
                bot.edit_message_text(f'⏳ <b>Running{dots}</b>', message.chat.id, msg_id)
            except:
                pass
            time.sleep(1)

    # List files
    if text == 'ls' or text.startswith('ls '):
        try:
            args = text.split()[1:] if len(text.split()) > 1 else ['.']
            path = args[0]
            full_path = os.path.abspath(path)
            
            if not full_path.startswith(os.path.abspath(f'users/{user_id}')):
                bot.reply_to(message, '❌ Access denied')
                return
            
            entries = os.listdir(full_path)
            file_list = []
            
            for entry in sorted(entries):
                entry_path = os.path.join(full_path, entry)
                if os.path.isdir(entry_path):
                    file_list.append(f'📁 {entry}/')
                else:
                    size = os.path.getsize(entry_path)
                    file_list.append(f'📄 {entry} ({format_bytes(size)})')
            
            if file_list:
                msg = f'📂 <b>Files in {path}:</b>\n\n' + '\n'.join(file_list[:50])
                if len(entries) > 50:
                    msg += f'\n\n<i>... and {len(entries) - 50} more</i>'
            else:
                msg = '📂 <b>Empty directory</b>'
            
            bot.reply_to(message, msg)
        except Exception as e:
            bot.reply_to(message, f'❌ Error: {str(e)}')
    
    # Change directory
    elif text.startswith('cd '):
        path = text[3:].strip()
        new_dir = os.path.abspath(path)
        if os.path.isdir(new_dir) and new_dir.startswith(os.path.abspath(f'users/{user_id}')):
            set_current_dir(user_id, new_dir)
            bot.reply_to(message, f'📂 Changed to: <code>{os.path.relpath(new_dir, f"users/{user_id}")}</code>')
        else:
            bot.reply_to(message, '❌ Invalid directory')
    
    # Git clone
    elif text.startswith('git clone '):
        cmd = text
        msg = bot.reply_to(message, '⏳ <b>Cloning repository...</b>')
        done_event = threading.Event()
        animator = threading.Thread(target=animate_progress, args=(msg.message_id, done_event))
        animator.start()
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=300)
            done_event.set()
            animator.join()
            bot.edit_message_text(f'✅ <b>Repository cloned</b>\n\n<code>{output.decode()[:500]}</code>', 
                                 message.chat.id, msg.message_id)
        except subprocess.TimeoutExpired:
            done_event.set()
            animator.join()
            bot.edit_message_text('❌ <b>Timeout:</b> Operation took too long', message.chat.id, msg.message_id)
        except Exception as e:
            done_event.set()
            animator.join()
            bot.edit_message_text(f'❌ <b>Error:</b> <code>{str(e)}</code>', message.chat.id, msg.message_id)
    
    # Package installation
    elif text.startswith(('pkg install ', 'pip install ', 'npm install ')):
        cmd = text
        msg = bot.reply_to(message, '⏳ <b>Installing package...</b>')
        done_event = threading.Event()
        animator = threading.Thread(target=animate_progress, args=(msg.message_id, done_event))
        animator.start()
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=300)
            done_event.set()
            animator.join()
            bot.edit_message_text(f'✅ <b>Package installed</b>\n\n<code>{output.decode()[-1000:]}</code>', 
                                 message.chat.id, msg.message_id)
        except Exception as e:
            done_event.set()
            animator.join()
            bot.edit_message_text(f'❌ <b>Error:</b> <code>{str(e)}</code>', message.chat.id, msg.message_id)
    
    # Sudo commands (Premium only)
    elif text.startswith('sudo '):
        if not is_premium(user_id):
            bot.reply_to(message, '⭐ <b>Premium feature only</b>')
            return
        
        cmd = text[5:]
        msg = bot.reply_to(message, '⏳ <b>Executing with elevated privileges...</b>')
        done_event = threading.Event()
        animator = threading.Thread(target=animate_progress, args=(msg.message_id, done_event))
        animator.start()
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=300)
            done_event.set()
            animator.join()
            bot.edit_message_text(f'✅ <b>Command executed</b>\n\n<code>{output.decode()[:2000]}</code>', 
                                 message.chat.id, msg.message_id)
        except Exception as e:
            done_event.set()
            animator.join()
            bot.edit_message_text(f'❌ <b>Error:</b> <code>{str(e)}</code>', message.chat.id, msg.message_id)
    
    # Run scripts
    elif text.startswith(('python ', 'node ', 'php ', 'bash ', 'sh ')):
        cmd = text
        service_id = str(uuid.uuid4())[:8]
        msg = bot.reply_to(message, f'🚀 <b>Starting service</b> <code>{service_id}</code>')
        
        try:
            proc = subprocess.Popen(
                cmd, 
                shell=True, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                cwd=current_dir
            )
            
            if user_id not in active_processes:
                active_processes[user_id] = {}
            active_processes[user_id][service_id] = proc
            
            # Persist to DB
            cur.execute('INSERT INTO processes (user_id, service_id, pid, command, started_at) VALUES (?, ?, ?, ?, DATETIME("now"))', 
                       (user_id, service_id, proc.pid, cmd))
            conn.commit()
            
            response = f'''
✅ <b>Service started successfully</b>

<b>Service ID:</b> <code>{service_id}</code>
<b>PID:</b> {proc.pid}
<b>Command:</b> <code>{cmd}</code>

Use <code>/log {service_id}</code> to view live logs
Use <code>stop {service_id}</code> to stop the service
'''
            bot.edit_message_text(response, message.chat.id, msg.message_id)
            
        except Exception as e:
            bot.edit_message_text(f'❌ <b>Error:</b> <code>{str(e)}</code>', message.chat.id, msg.message_id)
    
    # Stop service
    elif text.startswith('stop '):
        service_id = text[5:].strip()
        if user_id in active_processes and service_id in active_processes[user_id]:
            proc = active_processes[user_id][service_id]
            proc.kill()
            del active_processes[user_id][service_id]
            
            # Cleanup log streaming
            if service_id in log_streaming:
                del log_streaming[service_id]
            if service_id in service_logs:
                del service_logs[service_id]
            
            cur.execute('DELETE FROM processes WHERE service_id = ?', (service_id,))
            conn.commit()
            bot.reply_to(message, f'🛑 <b>Service stopped:</b> <code>{service_id}</code>')
        else:
            bot.reply_to(message, '❌ Service not found')
    
    # PWD - print working directory
    elif text == 'pwd':
        rel_path = os.path.relpath(current_dir, f'users/{user_id}')
        bot.reply_to(message, f'📍 <b>Current directory:</b>\n<code>~/{rel_path}</code>')
    
    # Clear screen (just send empty message)
    elif text in ['clear', 'cls']:
        bot.reply_to(message, '🧹 <i>Screen cleared</i>')
    
    # Echo command
    elif text.startswith('echo '):
        echo_text = text[5:]
        bot.reply_to(message, f'<code>{echo_text}</code>')
    
    # Cat command (view file)
    elif text.startswith('cat '):
        path = text[4:].strip()
        full_path = os.path.join(current_dir, path)
        if os.path.exists(full_path) and os.path.isfile(full_path) and full_path.startswith(f'users/{user_id}'):
            try:
                with open(full_path, 'r') as f:
                    content = f.read()[:3000]
                bot.reply_to(message, f'📄 <b>{path}</b>\n\n<code>{content}</code>')
            except Exception as e:
                bot.reply_to(message, f'❌ Error: <code>{str(e)}</code>')
        else:
            bot.reply_to(message, '❌ File not found or invalid')
    
    # Whoami
    elif text == 'whoami':
        status = '⭐ Premium User' if is_premium(user_id) else '🆓 Free User'
        bot.reply_to(message, f'👤 <b>User ID:</b> <code>{user_id}</code>\n<b>Status:</b> {status}')
    
    # Unknown command
    else:
        bot.reply_to(message, f'❓ Unknown command: <code>{text}</code>\n\nUse /help for available commands')

# Start bot
if __name__ == '__main__':
    print('🤖 Bot started...')
    bot.infinity_polling()
