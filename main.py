import os, time
from pyrofork import Client, filters
from pyrogram.errors import UserIsBlocked, PeerIdInvalid
from flask import Flask, render_template_string, abort
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from threading import Thread

# Load config
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
WEB_DOMAIN = os.getenv("WEB_DOMAIN")
MONGO_URI = os.getenv("MONGO_URI")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))

# DB setup
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["filestream"]
collection = db["files"]

# Flask App
app = Flask(__name__)

# Pyrogram Bot
bot = Client("FileStreamBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# HTML5 Player Template
PLAYER_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{{ title }}</title>
  <style>
    body { background-color: #111; color: #fff; text-align: center; font-family: sans-serif; }
    video { width: 90%; max-width: 800px; margin-top: 50px; border-radius: 12px; box-shadow: 0 0 20px #000; }
    h1 { font-size: 1.4rem; margin-top: 20px; }
    .footer { margin-top: 30px; font-size: 0.9rem; color: #888; }
  </style>
</head>
<body>
  <video controls autoplay>
    <source src="https://api.telegram.org/file/bot{{ bot_token }}/{{ file_path }}" type="{{ mime }}">
    Your browser does not support HTML5 video.
  </video>
  <h1>{{ title }}</h1>
  <div class="footer">Powered by FileStreamBot</div>
</body>
</html>
"""

@app.route('/')
def home():
    return "<h3>✅ FileStream Video Player Bot is Running!</h3>"

@app.route('/watch/<file_id>')
def stream_video(file_id):
    import requests

    # Find file info
    file = collection.find_one({"file_id": file_id})
    if not file:
        abort(404, description="File Not Found")

    file = file.result()
    # Get direct path
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
    res = requests.get(url).json()
    if not res.get("ok"):
        abort(500, description="Failed to fetch file path")

    file_path = res["result"]["file_path"]
    return render_template_string(PLAYER_TEMPLATE,
                                  title=file.get("file_name", "Untitled Video"),
                                  file_path=file_path,
                                  mime=file.get("mime_type", "video/mp4"),
                                  bot_token=BOT_TOKEN)

@bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) == 2:
        file_id = args[1]
        file = await collection.find_one({"file_id": file_id})
        if not file:
            await message.reply_text("❌ File not found.")
            return
        try:
            await client.send_cached_media(chat_id=message.chat.id, file_id=file_id,
                                           caption=file.get("caption", ""))
        except Exception as e:
            await message.reply_text(f"⚠️ Error: {e}")
    else:
        await message.reply_text("👋 Send me a video and I’ll give you a streaming link.")

@bot.on_message(filters.private & (filters.video | filters.document | filters.audio))
async def save_file(client, message):
    media = message.video or message.document or message.audio
    file_data = {
        "file_id": media.file_id,
        "file_unique_id": media.file_unique_id,
        "file_name": media.file_name,
        "mime_type": media.mime_type,
        "file_size": media.file_size,
        "caption": message.caption or "",
        "user_id": message.from_user.id,
        "timestamp": int(time.time())
    }

    # Save to DB
    await collection.update_one({"file_unique_id": media.file_unique_id},
                                {"$set": file_data}, upsert=True)

    stream_link = f"{WEB_DOMAIN}/watch/{media.file_id}"

    # Forward to Log Channel
    try:
        await client.send_cached_media(chat_id=LOG_CHANNEL, file_id=media.file_id,
            caption=f"👤 [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n🗂 `{media.file_name}`\n🔗 {stream_link}")
    except Exception as e:
        print("⚠️ Failed to send to LOG_CHANNEL:", e)

    await message.reply_text(f"✅ Saved!\n🔗 [Click to Watch]({stream_link})", disable_web_page_preview=True)

@bot.on_deleted_messages()
async def on_delete(client, messages):
    for msg in messages:
        user_id = msg.from_user.id if msg.from_user else None
        if user_id:
            result = await collection.delete_many({"user_id": user_id})
            print(f"🗑 Deleted {result.deleted_count} files of user {user_id}")

def run_web():
    app.run(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    Thread(target=run_web).start()
    bot.run()
