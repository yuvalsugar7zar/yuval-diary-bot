import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")
DATA_FILE = "meetings.json"

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def load_meetings():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_meetings(meetings):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(meetings, f, ensure_ascii=False, indent=2)

def parse_meeting_with_claude(text):
    today = datetime.now(ISRAEL_TZ)
    prompt = f"""היום הוא {today.strftime('%A %d/%m/%Y')}.
המשתמש שלח: "{text}"

האם זו בקשה להוסיף פגישה ליומן? אם כן, חלץ את הפרטים.
אם לא (למשל שאלה, בקשה למחוק, לראות יומן וכו') - ציין זאת.

ענה אך ורק ב-JSON תקין בפורמט הזה:
{{
  "is_meeting": true/false,
  "date": "DD/MM/YYYY",
  "time": "HH:MM",
  "location": "מיקום או ריק אם לא צוין",
  "subject": "נושא הפגישה",
  "action": "add/delete/list/unknown"
}}

לגבי תאריכים יחסיים:
- "מחר" = {(today + timedelta(days=1)).strftime('%d/%m/%Y')}
- "מחרתיים" = {(today + timedelta(days=2)).strftime('%d/%m/%Y')}
- "יום ראשון הקרוב" = תחשב לפי היום הנוכחי
- אם לא צוין תאריך, השתמש בתאריך הכי הגיוני

אם זה לא הוספת פגישה, שים is_meeting: false ו-action מתאים."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    
    text_response = response.content[0].text.strip()
    text_response = text_response.replace("```json", "").replace("```", "").strip()
    return json.loads(text_response)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    
    await update.message.reply_text("⏳ מעבד...")
    
    try:
        parsed = parse_meeting_with_claude(text)
        meetings = load_meetings()
        
        if parsed.get("action") == "list":
            today = datetime.now(ISRAEL_TZ).strftime("%d/%m/%Y")
            today_meetings = [m for m in meetings if m["date"] == today]
            if not today_meetings:
                await update.message.reply_text("📅 אין פגישות היום.")
            else:
                msg = "📅 *פגישות היום:*\n\n"
                for m in sorted(today_meetings, key=lambda x: x["time"]):
                    msg += f"🕐 {m['time']} - {m['subject']}"
                    if m.get("location"):
                        msg += f"\n📍 {m['location']}"
                    msg += "\n\n"
                await update.message.reply_text(msg, parse_mode="Markdown")
        
        elif parsed.get("is_meeting") and parsed.get("action") == "add":
            meeting = {
                "date": parsed["date"],
                "time": parsed["time"],
                "location": parsed.get("location", ""),
                "subject": parsed.get("subject", "פגישה"),
                "user_id": user_id,
                "reminded": False
            }
            meetings.append(meeting)
            save_meetings(meetings)
            
            msg = f"✅ *פגישה נוספה!*\n\n"
            msg += f"📅 תאריך: {meeting['date']}\n"
            msg += f"🕐 שעה: {meeting['time']}\n"
            msg += f"📋 נושא: {meeting['subject']}"
            if meeting["location"]:
                msg += f"\n📍 מיקום: {meeting['location']}"
            msg += "\n\n🔔 אזכיר לך שעתיים לפני!"
            
            await update.message.reply_text(msg, parse_mode="Markdown")
        
        elif parsed.get("action") == "delete":
            await update.message.reply_text("🗑️ לא הבנתי איזו פגישה למחוק. נסה לכתוב: 'מחק פגישה ב-[תאריך] בשעה [שעה]'")
        
        else:
            await update.message.reply_text(
                "לא הבנתי. נסה לכתוב למשל:\n"
                "• 'יש לי פגישה מחר ב-10:00 בתל אביב עם הצוות'\n"
                "• 'יש לי ישיבה ביום שלישי ב-14:00'\n"
                "• 'מה יש לי היום?'"
            )
    
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ משהו השתבש. נסה שוב.")

async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    meetings = load_meetings()
    tomorrow = (datetime.now(ISRAEL_TZ) + timedelta(days=1)).strftime("%d/%m/%Y")
    tomorrow_meetings = [m for m in meetings if m["date"] == tomorrow]
    
    all_users = set(m["user_id"] for m in meetings)
    
    for user_id in all_users:
        user_meetings = [m for m in tomorrow_meetings if m["user_id"] == user_id]
        
        if not user_meetings:
            msg = f"📅 *לו\"ז למחר ({tomorrow}):*\n\nאין פגישות מתוכננות מחר 😊"
        else:
            msg = f"📅 *לו\"ז למחר ({tomorrow}):*\n\n"
            for m in sorted(user_meetings, key=lambda x: x["time"]):
                msg += f"🕐 {m['time']} - {m['subject']}"
                if m.get("location"):
                    msg += f"\n📍 {m['location']}"
                msg += "\n\n"
        
        try:
            await context.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Could not send to {user_id}: {e}")

async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    meetings = load_meetings()
    now = datetime.now(ISRAEL_TZ)
    in_two_hours = now + timedelta(hours=2)
    changed = False
    
    for meeting in meetings:
        if meeting.get("reminded"):
            continue
        
        try:
            meeting_dt = datetime.strptime(f"{meeting['date']} {meeting['time']}", "%d/%m/%Y %H:%M")
            meeting_dt = ISRAEL_TZ.localize(meeting_dt)
            
            diff_minutes = (meeting_dt - now).total_seconds() / 60
            
            if 110 <= diff_minutes <= 130:
                msg = f"🔔 *תזכורת!*\n\n"
                msg += f"בעוד שעתיים יש לך:\n"
                msg += f"📋 {meeting['subject']}\n"
                msg += f"🕐 בשעה {meeting['time']}"
                if meeting.get("location"):
                    msg += f"\n📍 {meeting['location']}"
                
                await context.bot.send_message(
                    chat_id=int(meeting["user_id"]),
                    text=msg,
                    parse_mode="Markdown"
                )
                meeting["reminded"] = True
                changed = True
        except Exception as e:
            logger.error(f"Reminder error: {e}")
    
    if changed:
        save_meetings(meetings)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    job_queue = app.job_queue
    
    # שליחת לו"ז כל יום ב-20:00
    job_queue.run_daily(
        send_daily_schedule,
        time=datetime.strptime("20:00", "%H:%M").replace(tzinfo=ISRAEL_TZ).timetz()
    )
    
    # בדיקת תזכורות כל 10 דקות
    job_queue.run_repeating(send_reminders, interval=600, first=10)
    
    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
