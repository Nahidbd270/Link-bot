import os, time
from pyrofork import Client, filters
from pyrogram.errors import UserIsBlocked, PeerIdInvalid
from flask import Flask, redirect
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from threading import Thread

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
WEB_DOMAIN = os.getenv("WEB_DOMAIN")
MONGO_URI = os.getenv("MONGO_URI")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))

bot = Client("FileStreamBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["filestream"]
collection = db["files"]

app = Flask(__name__)

@app.route('/')
def home():
    return "<h3>✅ FileStream Bot Running</h3>"

@app.route('/watch/<file_id>')
def watch(file_id):
    return redirect(f"https://t.me/{BOT_USERNAME}?start={file_id}")

@bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) == 2:
        file_id = args[1]
        file = await collection.find_one({"file_id": file_id})
        if not file:
            await message.reply_text("❌ File not found or deleted.")
            return
        try:
            await client.send_cached_media(chat_id=message.chat.id, file_id=file_id, caption=file.get("caption", ""))
        except Exception as e:
            await message.reply_text(f"⚠️ Error sending file: {e}")
    else:
        await message.reply_text("👋 Send me a file and I’ll give you a permanent streaming link.")

@bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def handle_file(client, message):
    media = message.document or message.video or message.audio
    user_id = message.from_user.id
    file_data = {
        "file_id": media.file_id,
        "file_unique_id": media.file_unique_id,
        "user_id": user_id,
        "file_name": media.file_name,
        "mime_type": media.mime_type,
        "file_size": media.file_size,
        "caption": message.caption or "",
        "timestamp": int(time.time())
    }

    await collection.update_one({"file_unique_id": media.file_unique_id}, {"$set": file_data}, upsert=True)
    stream_link = f"{WEB_DOMAIN}/watch/{media.file_id}"

    # Forward to LOG CHANNEL
    try:
        await client.send_cached_media(chat_id=LOG_CHANNEL, file_id=media.file_id,
            caption=f"👤 User: [{message.from_user.first_name}](tg://user?id={user_id})\n🗂 File: `{media.file_name}`\n🔗 Link: {stream_link}")
    except Exception as e:
        print("⚠️ Failed to send to LOG_CHANNEL:", e)

    await message.reply_text(f"✅ File saved!\n🔗 [Stream Link]({stream_link})", disable_web_page_preview=True)

@bot.on_deleted_messages()
async def handle_deleted_user(client, messages):
    for msg in messages:
        user_id = msg.from_user.id if msg.from_user else None
        if user_id:
            result = await collection.delete_many({"user_id": user_id})
            print(f"❌ Deleted {result.deleted_count} files for user {user_id}")

async def check_deleted_users():
    users = await collection.distinct("user_id")
    for user_id in users:
        try:
            await bot.send_message(user_id, "Ping")
        except (UserIsBlocked, PeerIdInvalid):
            result = await collection.delete_many({"user_id": user_id})
            print(f"🚫 Auto-removed files for deleted user {user_id}: {result.deleted_count}")

def run_flask():
    app.run(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    Thread(target=run_flask).start()
    bot.run()
