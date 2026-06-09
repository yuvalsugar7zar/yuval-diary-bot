import os
import json
import re
import logging
from datetime import datetime, timedelta
import pytz
import cloudinary
import cloudinary.uploader
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")
ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS", "")

cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET
)

DATA_DIR = "/data"
os.makedirs(DATA_DIR, exist_ok=True)

MEETINGS_FILE = f"{DATA_DIR}/meetings.json"
MEMORY_FILE = f"{DATA_DIR}/memory.json"
NOTES_FILE = f"{DATA_DIR}/notes.json"
HISTORY_FILE = f"{DATA_DIR}/history.json"
SUMMARY_FILE = f"{DATA_DIR}/summary.json"
RECURRING_FILE = f"{DATA_DIR}/recurring.json"
REMINDERS_FILE = f"{DATA_DIR}/reminders.json"

MAX_HISTORY = 200
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def load_json(filename, default):
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return default

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_meetings(): return load_json(MEETINGS_FILE, [])
def save_meetings(d): save_json(MEETINGS_FILE, d)
def load_memory(): return load_json(MEMORY_FILE, {})
def save_memory(d): save_json(MEMORY_FILE, d)
def load_notes(): return load_json(NOTES_FILE, [])
def save_notes(d): save_json(NOTES_FILE, d)
def load_history(): return load_json(HISTORY_FILE, [])
def save_history(d): save_json(HISTORY_FILE, d)
def load_recurring(): return load_json(RECURRING_FILE, [])
def save_recurring(d): save_json(RECURRING_FILE, d)
def load_summary(): return load_json(SUMMARY_FILE, {"text": ""})
def save_summary(d): save_json(SUMMARY_FILE, d)
def load_reminders(): return load_json(REMINDERS_FILE, [])

def get_calendar_service():
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        logger.error(f"Calendar service error: {e}")
        return None

def add_to_google_calendar(meeting):
    try:
        service = get_calendar_service()
        if not service or not GOOGLE_CALENDAR_ID:
            return None
        
        date = meeting["date"]
        time = meeting["time"]
        dt_start = datetime.strptime(f"{date} {time}", "%d/%m/%Y %H:%M")
        dt_start = ISRAEL_TZ.localize(dt_start)
        dt_end = dt_start + timedelta(hours=1)
        
        event = {
            "summary": meeting["subject"],
            "location": meeting.get("location", ""),
            "start": {"dateTime": dt_start.isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": dt_end.isoformat(), "timeZone": "Asia/Jerusalem"},
        }
        
        result = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        return result.get("id")
    except Exception as e:
        logger.error(f"Google Calendar add error: {e}")
        return None

def delete_from_google_calendar(event_id):
    try:
        service = get_calendar_service()
        if not service or not event_id:
            return
        service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    except Exception as e:
        logger.error(f"Google Calendar delete error: {e}")
def save_reminders(d): save_json(REMINDERS_FILE, d)

def add_to_history(role, text):
    history = load_history()
    history.append({
        "role": role,
        "text": text[:200],
        "time": datetime.now(ISRAEL_TZ).strftime("%d/%m/%Y %H:%M")
    })
    if len(history) >= MAX_HISTORY:
        lines = [f"[{h['time']}] {h['role']}: {h['text']}" for h in history]
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": "סכם בקצרה (עד 200 מילה):\n" + "\n".join(lines)}]
        )
        existing = load_summary().get("text", "")
        new_text = (existing + "\n" + resp.content[0].text.strip()).strip()
        save_summary({"text": new_text[-3000:]})
        history = []
    save_history(history)

def get_meetings_text():
    meetings = load_meetings()
    if not meetings:
        return "אין פגישות"
    return "\n".join([f"{i}. {m['date']} {m['time']} - {m['subject']} - {m.get('location','')}" for i, m in enumerate(meetings)])

def get_notes_text():
    notes = load_notes()
    if not notes:
        return "אין פתקים"
    lines = []
    for i, n in enumerate(notes):
        line = f"{i}. [{n['time']}] {n['text']}"
        if n.get("image_url"):
            line += " 📷"
        lines.append(line)
    return "\n".join(lines)

def get_context():
    parts = []
    summary = load_summary().get("text", "")
    if summary:
        parts.append("היסטוריה: " + summary[-1000:])
    history = load_history()
    if history:
        parts.append("\n".join([f"{h['role']}: {h['text']}" for h in history[-20:]]))
    return "\n".join(parts) if parts else ""

def format_meetings_list(meetings_list, title):
    if not meetings_list:
        return f"{title}\n\nאין פגישות 😊"
    lines = [title, ""]
    for m in sorted(meetings_list, key=lambda x: (x["date"], x["time"])):
        lines.append(f"🕐 {m['date']} {m['time']} - *{m['subject']}*")
        if m.get("location"):
            lines.append(f"   📍 {m['location']}")
    return "\n".join(lines)

def classify_message(user_text):
    now = datetime.now(ISRAEL_TZ)
    memory = load_memory()

    system_prompt = """אתה מסווג פקודות. ענה תמיד ב-JSON תקין בלבד. אל תוסיף שום דבר מחוץ ל-JSON.
הפורמט חייב להיות בדיוק:
{"action":"...","response":"...","date":"","time":"","location":"","subject":"","extra_reminder":0,"field":"","value":"","index":-1,"note_text":"","note_topic":"","mem_key":"","mem_value":"","rec_type":"","rec_text":"","rec_time":"","rec_day":"","reminder_text":"","reminder_time":"","reminder_minutes":0}"""

    prompt = f"""עכשיו: {now.strftime('%d/%m/%Y %H:%M')}
זיכרון: {json.dumps(memory, ensure_ascii=False)}
פגישות: {get_meetings_text()}
פתקים: {get_notes_text()}
הקשר: {get_context()}

הודעה: {user_text}

סווג לאחת מהפעולות:
add_meeting, delete_meeting, edit_meeting, list_today, list_tomorrow, list_week, list_all, add_note, list_notes, delete_note, save_memory, add_recurring, list_recurring, delete_recurring, add_reminder, stats, chat

חוקים:
- add_meeting: date,time,location,subject. אם ביקש תזכורת נוספת שים extra_reminder=מספר דקות
- add_reminder: "בעוד X דקות" -> reminder_minutes=X | "ב-HH:MM" -> reminder_time="HH:MM"
- save_memory: mem_key,mem_value
- add_note: note_text,note_topic
- list_notes: note_topic לסינון או ריק

תאריכים: היום={now.strftime('%d/%m/%Y')}, מחר={(now+timedelta(days=1)).strftime('%d/%m/%Y')}

ענה JSON בלבד:"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r'```json|```', '', raw).strip()

    try:
        return json.loads(raw)
    except:
        match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
        logger.error(f"JSON parse failed: {raw[:200]}")
        return {"action": "chat", "response": raw[:300], "index": -1}

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    caption = update.message.caption or ""
    
    try:
        # הורד תמונה מטלגרם
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # העלה ל-Cloudinary
        import tempfile
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(file.file_path) as resp:
                img_data = await resp.read()
        
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(img_data)
            tmp_path = tmp.name
        
        result = cloudinary.uploader.upload(tmp_path, folder="yuval_diary")
        os.unlink(tmp_path)
        
        image_url = result["secure_url"]
        
        # שמור פתק עם תמונה
        now = datetime.now(ISRAEL_TZ)
        note = {
            "text": caption if caption else "תמונה",
            "topic": "",
            "time": now.strftime("%d/%m/%Y %H:%M"),
            "image_url": image_url
        }
        notes = load_notes()
        notes.append(note)
        save_notes(notes)
        
        resp = f"📷 *תמונה נשמרה!*\n\n📝 {note['text']}\n🕐 {note['time']}"
        add_to_history("משתמש", f"[תמונה] {caption}")
        add_to_history("בוט", resp)
        await update.message.reply_text(resp, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text("❌ לא הצלחתי לשמור את התמונה. נסה שוב.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    add_to_history("משתמש", text)

    try:
        r = classify_message(text)
        action = r.get("action", "chat")
        resp = r.get("response", "")

        meetings = load_meetings()
        notes = load_notes()
        memory = load_memory()
        recurring = load_recurring()
        reminders = load_reminders()
        now = datetime.now(ISRAEL_TZ)

        if action == "add_meeting":
            extra = int(r.get("extra_reminder", 0))
            m = {
                "date": r.get("date", ""),
                "time": r.get("time", ""),
                "location": r.get("location", ""),
                "subject": r.get("subject", "פגישה"),
                "user_id": user_id,
                "reminded": False,
                "extra_reminder_minutes": extra,
                "extra_reminded": False
            }
            meetings.append(m)
            save_meetings(meetings)
            # סנכרון עם גוגל קלנדר
            cal_event_id = add_to_google_calendar(m)
            if cal_event_id:
                meetings[-1]["google_event_id"] = cal_event_id
                save_meetings(meetings)
            resp = f"✅ *פגישה נוספה!*\n\n📅 {m['date']}\n🕐 {m['time']}\n📋 {m['subject']}"
            if m["location"]:
                resp += f"\n📍 {m['location']}"
            resp += "\n\n🔔 אזכיר לך שעתיים לפני!"
            if extra > 0 and extra != 120:
                resp += f"\n🔔 וגם {extra} דקות לפני!"
            if cal_event_id:
                resp += "\n📅 נוסף לגוגל קלנדר!"

        elif action == "delete_meeting":
            idx = int(r.get("index", -1))
            if 0 <= idx < len(meetings):
                deleted = meetings.pop(idx)
                save_meetings(meetings)
                # מחק מגוגל קלנדר
                if deleted.get("google_event_id"):
                    delete_from_google_calendar(deleted["google_event_id"])
                resp = f"🗑️ נמחקה: *{deleted['subject']}* ב-{deleted['date']} {deleted['time']}"
            else:
                resp = "❌ לא מצאתי. שאל 'מה הפגישות שלי?' לראות רשימה."

        elif action == "edit_meeting":
            idx = int(r.get("index", -1))
            field = r.get("field", "")
            value = r.get("value", "")
            if 0 <= idx < len(meetings) and field:
                old_val = meetings[idx].get(field, "")
                meetings[idx][field] = value
                if field in ["date", "time"]:
                    meetings[idx]["reminded"] = False
                    meetings[idx]["extra_reminded"] = False
                save_meetings(meetings)
                resp = f"✅ עודכן!\n*{meetings[idx]['subject']}*\n{field}: {old_val} ➜ {value}"
            else:
                resp = "❌ לא הצלחתי לעדכן."

        elif action == "list_today":
            today = now.strftime("%d/%m/%Y")
            resp = format_meetings_list([m for m in meetings if m["date"] == today], f"📅 *היום ({today}):*")

        elif action == "list_tomorrow":
            tomorrow = (now + timedelta(days=1)).strftime("%d/%m/%Y")
            resp = format_meetings_list([m for m in meetings if m["date"] == tomorrow], f"📅 *מחר ({tomorrow}):*")

        elif action == "list_week":
            dates = [(now + timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
            resp = format_meetings_list([m for m in meetings if m["date"] in dates], "📅 *השבוע:*")

        elif action == "list_all":
            resp = format_meetings_list(meetings, "📅 *כל הפגישות:*")

        elif action == "add_note":
            note = {
                "text": r.get("note_text", text),
                "topic": r.get("note_topic", ""),
                "time": now.strftime("%d/%m/%Y %H:%M")
            }
            notes.append(note)
            save_notes(notes)
            resp = f"📝 *נשמר!*\n\n{note['text']}\n🕐 {note['time']}"

        elif action == "list_notes":
            topic = r.get("note_topic", "")
            filtered = [n for n in notes if not topic or topic in n["text"] or topic in n.get("topic", "")]
            if not filtered:
                resp = "📝 אין פתקים."
            else:
                # שלח טקסט קודם
                lines = ["📝 *פתקים:*", ""]
                for n in filtered:
                    line = f"[{n['time']}] {n['text']}"
                    lines.append(line)
                resp = "\n".join(lines)
                await update.message.reply_text(resp, parse_mode="Markdown")
                # שלח תמונות בנפרד
                for n in filtered:
                    if n.get("image_url"):
                        await update.message.reply_photo(
                            photo=n["image_url"],
                            caption=f"📷 {n['text']} [{n['time']}]"
                        )
                add_to_history("בוט", resp)
                return

        elif action == "delete_note":
            idx = int(r.get("index", -1))
            if 0 <= idx < len(notes):
                notes.pop(idx)
                save_notes(notes)
                resp = "✅ הפתק נמחק!"

        elif action == "save_memory":
            key = r.get("mem_key", "")
            value = r.get("mem_value", "")
            if key:
                memory[key] = value
                save_memory(memory)
            resp = f"✅ זכרתי: {value}"

        elif action == "add_reminder":
            reminder_text = r.get("reminder_text", "תזכורת")
            reminder_minutes = int(r.get("reminder_minutes", 0))
            reminder_time_str = r.get("reminder_time", "")

            if reminder_minutes > 0:
                fire_at = now + timedelta(minutes=reminder_minutes)
            elif reminder_time_str:
                fire_at = datetime.strptime(f"{now.strftime('%d/%m/%Y')} {reminder_time_str}", "%d/%m/%Y %H:%M")
                fire_at = ISRAEL_TZ.localize(fire_at)
                if fire_at < now:
                    fire_at += timedelta(days=1)
            else:
                fire_at = now + timedelta(minutes=15)

            reminder = {
                "text": reminder_text,
                "fire_at": fire_at.strftime("%d/%m/%Y %H:%M"),
                "user_id": user_id,
                "sent": False
            }
            reminders.append(reminder)
            save_reminders(reminders)

            if reminder_minutes > 0:
                resp = f"⏰ *תזכורת נקבעה!*\n\n🔔 {reminder_text}\n🕐 בעוד {reminder_minutes} דקות ({fire_at.strftime('%H:%M')})"
            else:
                resp = f"⏰ *תזכורת נקבעה!*\n\n🔔 {reminder_text}\n🕐 ב-{fire_at.strftime('%H:%M')}"

        elif action == "add_recurring":
            rec = {
                "type": r.get("rec_type", "monthly"),
                "text": r.get("rec_text", ""),
                "time": r.get("rec_time", "09:00"),
                "day": str(r.get("rec_day", "1")),
                "user_id": user_id
            }
            recurring.append(rec)
            save_recurring(recurring)
            resp = f"✅ *תזכורת חוזרת נוספה!*\n🔔 {rec['text']}\n⏰ {rec['type']} בשעה {rec['time']}"

        elif action == "list_recurring":
            if not recurring:
                resp = "אין תזכורות חוזרות."
            else:
                lines = ["🔔 *תזכורות חוזרות:*", ""]
                for i, rec in enumerate(recurring):
                    lines.append(f"{i+1}. {rec['text']} - {rec['type']} {rec.get('time','')}")
                resp = "\n".join(lines)

        elif action == "delete_recurring":
            idx = int(r.get("index", -1))
            if 0 <= idx < len(recurring):
                recurring.pop(idx)
                save_recurring(recurring)
                resp = "✅ נמחק!"

        elif action == "stats":
            history = load_history()
            notes_with_img = len([n for n in notes if n.get("image_url")])
            summary = load_summary().get("text", "")
            resp = f"📊 *סטטיסטיקות:*\n\n"
            resp += f"📅 פגישות: {len(meetings)}\n"
            resp += f"📝 פתקים: {len(notes)}"
            if notes_with_img > 0:
                resp += f" (מתוכם {notes_with_img} עם תמונות)"
            resp += f"\n💬 הודעות בהיסטוריה: {len(history)}/{MAX_HISTORY}\n"
            resp += f"🔔 תזכורות חוזרות: {len(recurring)}\n"
            resp += f"🧠 סיכומים: {'יש ✅' if summary else 'אין עדיין'}\n"
            pending = [r for r in reminders if not r.get('sent')]
            resp += f"⏰ תזכורות ממתינות: {len(pending)}"

        add_to_history("בוט", resp)
        await update.message.reply_text(resp, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ משהו השתבש. נסה שוב.")

async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    meetings = load_meetings()
    tomorrow = (datetime.now(ISRAEL_TZ) + timedelta(days=1)).strftime("%d/%m/%Y")
    all_users = set(m["user_id"] for m in meetings)
    for user_id in all_users:
        user_meetings = [m for m in meetings if m["date"] == tomorrow and m["user_id"] == user_id]
        msg = format_meetings_list(user_meetings, f"📅 *לו\"ז מחר ({tomorrow}):*")
        try:
            await context.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Daily error: {e}")

async def send_weekly_schedule(context: ContextTypes.DEFAULT_TYPE):
    meetings = load_meetings()
    now = datetime.now(ISRAEL_TZ)
    dates = [(now + timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
    all_users = set(m["user_id"] for m in meetings)
    for user_id in all_users:
        user_meetings = [m for m in meetings if m["date"] in dates and m["user_id"] == user_id]
        msg = format_meetings_list(user_meetings, "📅 *לו\"ז לשבוע הקרוב:*")
        try:
            await context.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Weekly error: {e}")

async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    meetings = load_meetings()
    now = datetime.now(ISRAEL_TZ)
    changed = False

    for meeting in meetings:
        try:
            meeting_dt = datetime.strptime(f"{meeting['date']} {meeting['time']}", "%d/%m/%Y %H:%M")
            meeting_dt = ISRAEL_TZ.localize(meeting_dt)
            diff = (meeting_dt - now).total_seconds() / 60

            if not meeting.get("reminded") and 110 <= diff <= 130:
                msg = f"🔔 *תזכורת!*\n\nבעוד שעתיים:\n📋 *{meeting['subject']}*\n🕐 {meeting['time']}"
                if meeting.get("location"):
                    msg += f"\n📍 {meeting['location']}"
                await context.bot.send_message(chat_id=int(meeting["user_id"]), text=msg, parse_mode="Markdown")
                meeting["reminded"] = True
                changed = True

            extra = int(meeting.get("extra_reminder_minutes", 0))
            if extra > 0 and not meeting.get("extra_reminded") and (extra - 3) <= diff <= (extra + 3):
                msg = f"🔔 *תזכורת!*\n\nבעוד {extra} דקות:\n📋 *{meeting['subject']}*\n🕐 {meeting['time']}"
                if meeting.get("location"):
                    msg += f"\n📍 {meeting['location']}"
                await context.bot.send_message(chat_id=int(meeting["user_id"]), text=msg, parse_mode="Markdown")
                meeting["extra_reminded"] = True
                changed = True

        except Exception as e:
            logger.error(f"Reminder error: {e}")

    if changed:
        save_meetings(meetings)

    reminders = load_reminders()
    changed_r = False
    for reminder in reminders:
        if reminder.get("sent"):
            continue
        try:
            fire_at = datetime.strptime(reminder["fire_at"], "%d/%m/%Y %H:%M")
            fire_at = ISRAEL_TZ.localize(fire_at)
            if abs((fire_at - now).total_seconds()) <= 90:
                msg = f"🔔 *תזכורת!*\n\n{reminder['text']}"
                await context.bot.send_message(chat_id=int(reminder["user_id"]), text=msg, parse_mode="Markdown")
                reminder["sent"] = True
                changed_r = True
        except Exception as e:
            logger.error(f"One-time reminder error: {e}")

    if changed_r:
        save_reminders(reminders)

async def check_recurring(context: ContextTypes.DEFAULT_TYPE):
    recurring = load_recurring()
    now = datetime.now(ISRAEL_TZ)
    hm = now.strftime("%H:%M")
    for rec in recurring:
        if hm != rec.get("time", ""):
            continue
        send = False
        t = rec.get("type", "")
        day = str(rec.get("day", "1"))
        if t == "daily":
            send = True
        elif t == "monthly" and str(now.day) == day:
            send = True
        elif t == "day_of_week":
            day_map = {"0": 6, "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5}
            if day_map.get(day, -1) == now.weekday():
                send = True
        if send:
            try:
                await context.bot.send_message(chat_id=int(rec["user_id"]), text=f"🔔 *תזכורת:*\n{rec['text']}", parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Recurring error: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    jq = app.job_queue
    jq.run_daily(send_daily_schedule, time=datetime.strptime("21:00", "%H:%M").replace(tzinfo=ISRAEL_TZ).timetz())
    jq.run_daily(send_weekly_schedule, time=datetime.strptime("21:00", "%H:%M").replace(tzinfo=ISRAEL_TZ).timetz(), days=(5,))
    jq.run_repeating(send_reminders, interval=60, first=10)
    jq.run_repeating(check_recurring, interval=60, first=5)
    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
