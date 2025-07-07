import os
import time
import asyncio
from pyrofork import Client, filters
from pyrogram.errors import UserIsBlocked, PeerIdInvalid
from flask import Flask, render_template_string, abort
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from threading import Thread
import aiohttp # requests এর পরিবর্তে aiohttp ব্যবহার করা হয়েছে

# .env ফাইল থেকে কনফিগারেশন লোড করা হচ্ছে
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
WEB_DOMAIN = os.getenv("WEB_DOMAIN") # যেমন: https://your-domain.com
MONGO_URI = os.getenv("MONGO_URI")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))

# MongoDB ডাটাবেস সেটআপ
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["filestream"]
collection = db["files"]

# Flask অ্যাপ ইনিশিয়ালাইজেশন
app = Flask(__name__)

# Pyrogram বট ক্লায়েন্ট ইনিশিয়ালাইজেশন
bot = Client("FileStreamBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# HTML5 প্লেয়ারের জন্য টেমপ্লেট
PLAYER_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{{ title }}</title>
  <style>
    body { background-color: #0d1117; color: #c9d1d9; text-align: center; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji"; }
    video { width: 95%; max-width: 850px; margin-top: 30px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4); outline: none; }
    h1 { font-size: 1.5rem; margin-top: 20px; font-weight: 600; }
    .footer { margin-top: 30px; font-size: 0.9rem; color: #8b949e; }
    a { color: #58a6ff; text-decoration: none; }
  </style>
</head>
<body>
  <video controls autoplay playsinline>
    <source src="https://api.telegram.org/file/bot{{ bot_token }}/{{ file_path }}" type="{{ mime }}">
    আপনার ব্রাউজার HTML5 ভিডিও সাপোর্ট করে না।
  </video>
  <h1>{{ title }}</h1>
  <div class="footer">Powered by <a href="https://t.me/{{ bot_username }}">{{ bot_username }}</a></div>
</body>
</html>
"""

# Flask হোমপেজ রুট
@app.route('/')
def home():
    return "<h3>✅ ফাইলস্ট্রিম বট চলছে!</h3>"

# ভিডিও স্ট্রিম করার জন্য Flask রুট
# এই ফাংশনটিকে async করা হয়েছে কারণ এটি await ব্যবহার করে
@app.route('/watch/<file_id>')
async def stream_video(file_id):
    # ডাটাবেস থেকে ফাইলের তথ্য খোঁজা হচ্ছে
    file_doc = await collection.find_one({"file_id": file_id})
    if not file_doc:
        return abort(404, description="ফাইল খুঁজে পাওয়া যায়নি। লিঙ্কটি ভুল হতে পারে বা ফাইলটি ডিলিট করা হয়েছে।")

    # Telegram API থেকে ফাইলের পাথ (path) আনা হচ্ছে
    # এখানে aiohttp ব্যবহার করা হয়েছে যাতে অ্যাপ ব্লক না হয়
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        async with session.get(url) as response:
            if response.status != 200:
                return abort(500, description="Telegram API থেকে ফাইলের তথ্য আনতে ব্যর্থ।")
            
            res = await response.json()
            if not res.get("ok"):
                return abort(500, description="Telegram API error: " + res.get("description", "Unknown error"))

    file_path = res["result"]["file_path"]
    
    return render_template_string(
        PLAYER_TEMPLATE,
        title=file_doc.get("file_name", "Untitled Video"),
        file_path=file_path,
        mime=file_doc.get("mime_type", "video/mp4"),
        bot_token=BOT_TOKEN,
        bot_username=BOT_USERNAME
    )

# '/start' কমান্ড হ্যান্ডলার
@bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    args = message.text.split(maxsplit=1)
    # ডীপ লিঙ্কিং চেক করা হচ্ছে (e.g., t.me/bot?start=file_id)
    if len(args) == 2:
        file_id = args[1]
        file_doc = await collection.find_one({"file_id": file_id})
        if not file_doc:
            await message.reply_text("❌ ফাইল খুঁজে পাওয়া যায়নি বা লিঙ্কটি অবৈধ।")
            return
        try:
            # সরাসরি ফাইলটি ব্যবহারকারীকে পাঠানো হচ্ছে
            await client.send_cached_media(
                chat_id=message.chat.id, 
                file_id=file_id,
                caption=file_doc.get("caption", "")
            )
        except Exception as e:
            await message.reply_text(f"⚠️ ফাইলটি পাঠাতে সমস্যা হয়েছে: {e}")
    else:
        await message.reply_text("👋 আমাকে একটি ভিডিও, অডিও বা ডকুমেন্ট পাঠান এবং আমি আপনাকে একটি স্ট্রিমিং লিঙ্ক দেব।")

# ভিডিও, অডিও বা ডকুমেন্ট মেসেজ হ্যান্ডলার
@bot.on_message(filters.private & (filters.video | filters.document | filters.audio))
async def save_file(client, message):
    media = message.video or message.document or message.audio
    
    # media অবজেক্ট না থাকলে কিছুই করবে না
    if not media:
        return

    # ফাইলের মেটাডেটা একটি ডিকশনারিতে সাজানো হচ্ছে
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

    # ডাটাবেসে ফাইল সেভ বা আপডেট করা হচ্ছে
    await collection.update_one(
        {"file_unique_id": media.file_unique_id},
        {"$set": file_data},
        upsert=True
    )

    stream_link = f"{WEB_DOMAIN}/watch/{media.file_id}"
    
    # লগ চ্যানেলে ফাইলের তথ্য ফরওয়ার্ড করা হচ্ছে
    try:
        await client.send_cached_media(
            chat_id=LOG_CHANNEL, 
            file_id=media.file_id,
            caption=(
                f"👤 ব্যবহারকারী: [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n"
                f"🆔 ইউজার আইডি: `{message.from_user.id}`\n\n"
                f"🗂 ফাইলের নাম: `{media.file_name}`\n"
                f"🔗 স্ট্রিমিং লিঙ্ক: {stream_link}"
            )
        )
    except Exception as e:
        print(f"⚠️ লগ চ্যানেলে পাঠাতে ব্যর্থ: {e}")

    await message.reply_text(
        f"✅ ফাইল সেভ হয়েছে!\n\n🔗 **আপনার স্ট্রিমিং লিঙ্ক:**\n{stream_link}", 
        disable_web_page_preview=True
    )

# [বিঃদ্রঃ] এই হ্যান্ডলারটি ঝুঁকিপূর্ণ কারণ এটি একজন ব্যবহারকারীর যেকোনো মেসেজ ডিলেটের জন্য তার *সমস্ত* ফাইল ডিলেট করে দেবে।
# নির্ভরযোগ্যভাবে নির্দিষ্ট ফাইল ডিলেট করা কঠিন। তাই এটি নিষ্ক্রিয় রাখা হলো।
# @bot.on_deleted_messages()
# async def on_delete(client, messages):
#     for msg in messages:
#         # ডিলিট হওয়া মেসেজ থেকে ফাইল আইডি পাওয়া নির্ভরযোগ্য নয়
#         # তাই এই লজিক ব্যবহার না করাই ভালো
#         pass


# Flask অ্যাপটি একটি আলাদা থ্রেডে চালানোর জন্য ফাংশন
def run_web():
    # প্রোডাকশনের জন্য Gunicorn ব্যবহার করা ভালো, তবে ডেভেলপমেন্টের জন্য এটি ঠিক আছে
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    # Flask ওয়েবসার্ভার শুরু করা হচ্ছে
    web_thread = Thread(target=run_web)
    web_thread.start()
    
    # Pyrogram বট শুরু করা হচ্ছে
    print("🤖 বট চালু হচ্ছে...")
    bot.run()
