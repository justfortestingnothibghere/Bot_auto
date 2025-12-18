from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import shutil
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import uuid

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

UPLOAD_DIR = "./files"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)

# DB (simple file-based)
DB = "files.json"
def load_db():
    if os.path.exists(DB):
        import json
        with open(DB) as f:
            return json.load(f)
    return {}

def save_db(data):
    import json
    with open(DB, "w") as f:
        json.dump(data, f, indent=2)

# Auto delete old files
def cleanup_old_files():
    db = load_db()
    deleted = 0
    for key, info in list(db.items()):
        if datetime.fromisoformat(info["expires"]) < datetime.now():
            path = info["path"]
            if os.path.exists(path):
                os.remove(path)
            del db[key]
            deleted += 1
    save_db(db)
    print(f"Cleaned {deleted} old files")

scheduler = BackgroundScheduler()
scheduler.add_job(cleanup_old_files, "interval", hours=6)
scheduler.start()


def keep_alive():
    while True:
        try:
            requests.get("https://hostaitelegrambot.onrender/ping")
        except:
            pass
        time.sleep(25)  # Less than 50 sec Render timeout

threading.Thread(target=keep_alive, daemon=True).start()

@app.route("/ping")
def ping():
    return "OK", 200
    
# Beautiful Download Page
@app.get("/d/{file_id}")
async def download_page(file_id: str):
    db = load_db()
    if file_id not in db:
        raise HTTPException(404, "File not found or expired")
    
    info = db[file_id]
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Download • MirrorBot</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@600&family=Roboto&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
        <style>
            body {{ font-family: 'Poppins', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); margin:0; height:100vh; display:flex; align-items:center; justify-content:center; color:white; }}
            .card {{ background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border-radius: 20px; padding: 40px; text-align:center; box-shadow: 0 10px 30px rgba(0,0,0,0.3); max-width: 500px; width: 90%; }}
            h1 {{ font-size: 2.5em; margin: 0; }}
            p {{ opacity: 0.9; }}
            .btn {{ margin-top: 30px; padding: 15px 40px; background: #ff6b6b; color: white; border: none; border-radius: 50px; font-size: 1.2em; cursor: pointer; text-decoration: none; display: inline-block; }}
            .btn:hover {{ background: #ff5252; transform: scale(1.05); transition: 0.3s; }}
            .info {{ margin: 20px 0; background: rgba(0,0,0,0.2); padding: 15px; border-radius: 15px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Your File is Ready!</h1>
            <div class="info">
                <p><strong>Filename:</strong> {info['filename']}</p>
                <p><strong>Size:</strong> {info['size_mb']:.1f} MB</p>
                <p><strong>Expires in:</strong> {info['expires_in']} days</p>
            </div>
            <a href="/download/{file_id}" class="btn">
                Download Now
            </a>
            <p style="margin-top:30px; font-size:0.9em;">Made with ❤️ by @MR_ARMAN_08</p>
        </div>
    </body>
    </html>
    """)

@app.get("/download/{file_id}")
async def download_file(file_id: str):
    db = load_db()
    if file_id not in db:
        raise HTTPException(404, "File expired or not found")
    path = db[file_id]["path"]
    if not os.path.exists(path):
        del db[file_id]
        save_db(db)
        raise HTTPException(404, "File deleted")
    return FileResponse(path, filename=db[file_id]["filename"])

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())[:8]
    filepath = os.path.join(UPLOAD_DIR, f"{file_id}_{file.filename}")
    
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    size_mb = os.path.getsize(filepath) / (1024*1024)
    expires = (datetime.now() + timedelta(days=7)).isoformat()
    
    db = load_db()
    db[file_id] = {
        "filename": file.filename,
        "path": filepath,
        "size_mb": size_mb,
        "uploaded": datetime.now().isoformat(),
        "expires": expires,
        "expires_in": 7
    }
    save_db(db)
    
    return {"id": file_id, "url": f"https://mirror.arman.in/d/{file_id}"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
