import os
import time
import asyncio
import aiohttp
from pyrogram import Client, filters
from pyrogram.errors import UserIsBlocked, PeerIdInvalid
from flask import Flask, render_template_string, abort, redirect, url_for
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# .env ফাইল থেকে কনফিগারেশন লোড করা হচ্ছে
load_dotenv()

# --- Configuration ---
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
WEB_DOMAIN = os.getenv("WEB_DOMAIN")
MONGO_URI = os.getenv("MONGO_URI")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))

# --- Database Setup ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["filestream"]
files_collection = db["files"]

# --- Flask App ---
app = Flask(__name__)

# --- Pyrogram Bot ---
# 'bot_token' এখানে না দিয়ে 'Client' এর বাইরে রাখাই ভালো অভ্যাস
bot = Client(
    "FileStreamBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- HTML Template ---
PLAYER_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ title }}</title>
  <style>
    body { background-color: #0d1117; color: #c9d1d9; text-align: center; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif; margin: 0; padding: 20px; }
    .container { max-width: 850px; margin: auto; }
    video { width: 100%; border-radius: 12px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5); outline: none; margin-top: 20px; }
    h1 { font-size: 1.6rem; margin-top: 25px; font-weight: 600; }
    .footer { margin-top: 30px; font-size: 0.9rem; color: #8b949e; }
    a { color: #58a6ff; text-decoration: none; }
    a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="container">
    <video controls autoplay playsinline>
      <source src="{{ stream_url }}" type="{{ mime_type }}">
      Your browser does not support the video tag.
    </video>
    <h1>{{ title }}</h1>
    <div class="footer">Powered by <a href="https://t.me/{{ bot_username }}" target="_blank">{{ bot_username }}</a></div>
  </div>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/')
def home():
    return "<h3>✅ ফাইলস্ট্রিম বট সফলভাবে চলছে!</h3>"

@app.route('/watch/<file_id>')
async def watch_stream(file_id):
    file_doc = await files_collection.find_one({"file_id": file_id})
    if not file_doc:
        return abort(404, "ফাইল খুঁজে পাওয়া যায়নি। লিঙ্কটি ভুল হতে পারে বা ফাইলটি মুছে ফেলা হয়েছে।")

    # টেলিগ্রাম থেকে সরাসরি ফাইল ডাউনলোড লিঙ্ক তৈরি হয় না, তাই রিডাইরেক্ট করতে হবে
    # তবে এই ক্ষেত্রে, আমরা সরাসরি Telegram API-এর ফাইল পাথ ব্যবহার করবো
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
            async with session.get(url) as resp:
                if resp.status != 200:
                    return abort(502, "Telegram API থেকে ফাইল তথ্য আনতে ব্যর্থ।")
                data = await resp.json()
                if not data.get("ok"):
                    return abort(404, f"Telegram error: {data.get('description')}")
        
        file_path = data['result']['file_path']
        stream_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

        return render_template_string(
            PLAYER_TEMPLATE,
            title=file_doc.get("file_name", "Untitled Video"),
            stream_url=stream_url,
            mime_type=file_doc.get("mime_type", "video/mp4"),
            bot_username=BOT_USERNAME
        )
    except Exception as e:
        print(f"Error in watch_stream: {e}")
        return abort(500, "সার্ভারে একটি অপ্রত্যাশিত ত্রুটি ঘটেছে।")


# --- Pyrogram Handlers ---
@bot.on_message(filters.private & filters.command("start"))
async def start_handler(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        file_id = args[1]
        file_doc = await files_collection.find_one({"file_id": file_id})
        if file_doc:
            try:
                await client.send_cached_media(
                    chat_id=message.chat.id,
                    file_id=file_id,
                    caption=file_doc.get("caption", "")
                )
            except Exception as e:
                await message.reply_text(f"⚠️ ফাইলটি পাঠাতে একটি ত্রুটি হয়েছে: {e}")
        else:
            await message.reply_text("❌ ফাইলটি খুঁজে পাওয়া যায়নি বা লিঙ্কটি অবৈধ।")
    else:
        await message.reply_text(
            f"👋 স্বাগতম!\n\nআমাকে যেকোনো ভিডিও, অডিও বা ডকুমেন্ট ফাইল পাঠান, "
            f"এবং আমি আপনাকে একটি সরাসরি স্ট্রিমিং লিঙ্ক দেব।\n\n"
            f"তৈরি করেছেন [Your Name](Your_Profile_Link)।" # আপনার নাম ও প্রোফাইল লিঙ্ক দিন
        )


@bot.on_message(filters.private & (filters.video | filters.document | filters.audio))
async def file_handler(client, message):
    media = message.video or message.document or message.audio
    if not media:
        return

    # ফাইল মেটাডেটা প্রস্তুত করা হচ্ছে
    file_data = {
        "file_id": media.file_id,
        "file_unique_id": media.file_unique_id,
        "file_name": media.file_name or "Untitled",
        "mime_type": media.mime_type,
        "file_size": media.file_size,
        "caption": message.caption.html if message.caption else "",
        "user_id": message.from_user.id,
        "timestamp": int(time.time())
    }

    # ডাটাবেসে সেভ করা হচ্ছে
    await files_collection.update_one(
        {"file_unique_id": media.file_unique_id},
        {"$set": file_data},
        upsert=True
    )

    stream_link = f"{WEB_DOMAIN}/watch/{media.file_id}"

    # ব্যবহারকারীকে লিঙ্ক পাঠানো হচ্ছে
    await message.reply_text(
        f"✅ ফাইল সফলভাবে সেভ হয়েছে!\n\n"
        f"🔗 **আপনার স্ট্রিমিং লিঙ্ক:**\n`{stream_link}`",
        disable_web_page_preview=True
    )

    # লগ চ্যানেলে তথ্য পাঠানো হচ্ছে
    try:
        log_caption = (
            f"👤 **ব্যবহারকারী:** [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n"
            f"🆔 **আইডি:** `{message.from_user.id}`\n\n"
            f"🗂️ **ফাইলের নাম:** `{file_data['file_name']}`\n"
            f"🔗 **লিঙ্ক:** {stream_link}"
        )
        await client.send_message(chat_id=LOG_CHANNEL, text=log_caption)
    except Exception as e:
        print(f"⚠️ লগ চ্যানেলে পাঠাতে ব্যর্থ: {e}")

# বট এবং ওয়েব অ্যাপ একসাথে চালানোর জন্য
async def run_all():
    await bot.start()
    print("🤖 বট সফলভাবে চালু হয়েছে!")
    # Gunicorn এই অংশটি চালাবে, তাই এখানে সরাসরি app.run() নেই।
    # For local testing, you can add `app.run(...)` here inside an `if __name__ == "__main__"` block
    await asyncio.Event().wait() # বটকে সচল রাখে

# Koyeb বা Heroku সরাসরি 'app' অবজেক্টটি খুঁজবে
# Gunicorn এটি ব্যবহার করবে: gunicorn bot:app
if __name__ == '__main__':
    # এই অংশটি শুধুমাত্র লোকালভাবে চালানোর জন্য
    # ডেপ্লয়মেন্টে এটি ব্যবহৃত হবে না
    print("লোকালভাবে বট চালানো হচ্ছে...")
    asyncio.run(run_all())
