# -*- coding: utf-8 -*-
"""
بات تلگرامی ارسال آهنگ با کد - نسخه Webhook برای هاست رایگان روی رندر
دیتابیس: MongoDB Atlas (رایگان)

نیازمندی‌ها: pip install -r requirements.txt
"""

import os
import telebot
from telebot import types
from flask import Flask, request
from pymongo import MongoClient

# ============ تنظیمات (از Environment Variables رندر می‌خونیم) ============
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
MONGO_URI = os.environ.get("MONGO_URI")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # مثلا: https://your-app.onrender.com
# ============================================================================

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["song_bot_db"]
songs_collection = db["songs"]

# حافظه موقت برای مراحل ثبت آهنگ توسط ادمین (برای چند ادمین هم جواب میده)
admin_state = {}


def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("ثبت آهنگ"))
    return kb


@bot.message_handler(commands=["start"])
def start(message):
    welcome_text = (
        "سلام خوش اومدی😍\n"
        "کد آهنگ رو بفرست تا آهنگ رو برات بفرستم🌹\n"
        "مثال : 1 ، 2 و...✅"
    )
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, welcome_text, reply_markup=admin_keyboard())
    else:
        bot.send_message(message.chat.id, welcome_text, reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and m.text == "ثبت آهنگ")
def register_song_start(message):
    admin_state[message.from_user.id] = {"step": "wait_code"}
    bot.send_message(message.chat.id, "کد آهنگ رو بفرست (مثلا 1 یا 2):")


@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and
                      admin_state.get(m.from_user.id, {}).get("step") == "wait_code")
def register_song_code(message):
    code = message.text.strip()
    admin_state[message.from_user.id] = {"step": "wait_song", "code": code}
    bot.send_message(message.chat.id, f"باشه، حالا آهنگ مربوط به کد «{code}» رو بفرست 🎵")


@bot.message_handler(content_types=["audio", "voice", "document"],
                      func=lambda m: m.from_user.id == ADMIN_ID and
                      admin_state.get(m.from_user.id, {}).get("step") == "wait_song")
def register_song_file(message):
    code = admin_state[message.from_user.id]["code"]

    if message.content_type == "audio":
        file_id = message.audio.file_id
        file_type = "audio"
    elif message.content_type == "voice":
        file_id = message.voice.file_id
        file_type = "voice"
    else:
        file_id = message.document.file_id
        file_type = "document"

    songs_collection.update_one(
        {"code": code},
        {"$set": {"file_id": file_id, "type": file_type}},
        upsert=True
    )

    admin_state.pop(message.from_user.id, None)
    bot.send_message(message.chat.id, f"آهنگ با کد «{code}» ثبت شد ✅", reply_markup=admin_keyboard())


@bot.message_handler(func=lambda m: True, content_types=["text"])
def send_song(message):
    if message.from_user.id == ADMIN_ID and message.text == "ثبت آهنگ":
        return  # قبلا هندل شده

    code = message.text.strip()
    doc = songs_collection.find_one({"code": code})

    if doc:
        if doc["type"] == "audio":
            bot.send_audio(message.chat.id, doc["file_id"])
        elif doc["type"] == "voice":
            bot.send_voice(message.chat.id, doc["file_id"])
        else:
            bot.send_document(message.chat.id, doc["file_id"])
    else:
        bot.send_message(message.chat.id, "آهنگی با این کد پیدا نشد ❌")


# ============ بخش وبهوک (Flask) ============

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200


@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200


@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    return "Webhook set!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
  
