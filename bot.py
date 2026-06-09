import os
import json
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

DATA_DIR = "/data"
os.makedirs(DATA_DIR, exist_ok=True)

MEETINGS_FILE = f"{DATA_DIR}/meetings.json"
MEMORY_FILE = f"{DATA_DIR}/memory.json"
NOTES_FILE = f"{DATA_DIR}/notes.json"
HISTORY_FILE = f"{DATA_DIR}/history.json"
SUMMARY_FILE = f"{DATA_DIR}/summary.json"
RECURRING_FILE = f"{DATA_DIR}/recurring.json"

MAX_HISTORY = 200
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def load_json(filename, default):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
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
def load_summary(): return load_json(SUMMARY_FILE, {"text": "", "updated": ""})
def save_summary(d): save_json(SUMMARY_FILE, d)

def summarize_history(history):
    if not history:
        return ""
    lines = [f"[{h['time']}] {h['role']}: {h['text']}" for h in history]
    history_text = "\n".join(lines)
    
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": f"""סכם את השיחה הבאה בקצרה (עד 300 מילה).
שמור על: פגישות שנדונו, החלטות חשובות, מידע אישי שהוזכר, נושאים עיקריים.

שיחה:
{history_text}

סיכום:"""}]
    )
    return response.content[0].text.strip()

def add_to_history(role, text):
    history = load_history()
    history.append({
        "role": role,
        "text": text[:200],
        "time": datetime.now(ISRAEL_TZ).strftime("%d/%m/%Y %H:%M")
    })
    
    if len(history) >= MAX_HISTORY:
        logger.info("History full - summarizing...")
        new_summary_text = summarize_history(history)
        existing = load_summary()
        if existing["text"]:
            combined = f"{existing['text']}\n\nסיכום נוסף ({datetime.now(ISRAEL_TZ).strftime('%d/%m/%Y')}):\n{new_summary_text}"
            final_summary = combined
        else:
            final_summary = new_summary_text
        save_summary({"text": final_summary, "updated": datetime.now(ISRAEL_TZ).strftime("%d/%m/%Y %H:%M")})
        history = []
    
    save_history(history)

def get_context_text():
    summary = load_summary()
    history = load_history()
    
    parts = []
    if summary["text"]:
        parts.append(f"סיכום שיחות קודמות:\n{summary['text']}")
    if history:
        lines = [f"[{h['time']}] {h['role']}: {h['text']}" for h in history[-40:]]
        parts.append(f"שיחה נוכחית:\n" + "\n".join(lines))
    
    return "\n\n".join(parts) if parts else "אין היסטוריה."

def get_meetings_text():
    meetings = load_meetings()
    if not meetings:
        return "אין פגישות."
    return "\n".join([f"{i}. {m['date']} {m['time']} - {m['subject']} | מיקום: {m.get('location','לא צוין')}" for i, m in enumerate(meetings)])

def get_notes_text():
    notes = load_notes()
    if not notes:
        return "אין פתקים."
    return "\n".join([f"{i}. [{n['time']}] {n['text']}" for i, n in enumerate(notes)])

def get_recurring_text():
    recurring = load_recurring()
    if not recurring:
        return "אין תזכורות חוזרות."
    return "\n".join([f"{i}. {r['type']} - {r['text']} (שעה: {r.get('time','')})" for i, r in enumerate(recurring)])

def format_meetings_list(meetings_list, title):
    if not meetings_list:
        return f"{title}\n\nאין פגישות 😊"
    lines = [title, ""]
    for m in sorted(meetings_list, key=lambda x: (x["date"], x["time"])):
        lines.append(f"🕐 {m['date']} {m['time']} - *{m['subject']}*")
        if m.get("location"):
            lines.append(f"   📍 {m['location']}")
    return "\n".join(lines)

def process_message(user_text):
    now = datetime.now(ISRAEL_TZ)
    memory = load_memory()
    memory_str = json.dumps(memory, ensure_ascii=False) if memory else "ריק"

    prompt = f"""אתה עוזר אישי חכם. עכשיו: {now.strftime('%A %d/%m/%Y %H:%M')}.

זיכרון על המשתמש:
{memory_str}

כל הפגישות (עם מספר שורה):
{get_meetings_text()}

כל הפתקים (עם מספר שורה):
{get_notes_text()}

תזכורות חוזרות:
{get_recurring_text()}

היסטוריה ושיחות קודמות:
{get_context_text()}

המשתמש כתב: "{user_text}"

ענה ב-JSON בלבד (ללא markdown):
{{
  "action": "add_meeting|delete_meeting|edit_meeting|list_today|list_tomorrow|list_week|list_all|add_note|list_notes|delete_note|save_memory|add_recurring|list_recurring|delete_recurring|chat",
  "response": "תשובה בעברית למשתמש",
  "data": {{}}
}}

כללים:
- add_meeting: data={{"date":"DD/MM/YYYY","time":"HH:MM","location":"","subject":""}}
- delete_meeting: data={{"index":מספר}}
- edit_meeting: data={{"index":מספר,"field":"time|location|subject|date","value":"ערך חדש"}}
- add_note: data={{"text":"","topic":""}}
- list_notes: data={{"topic":""}}
- delete_note: data={{"index":מספר}}
- save_memory: data={{"key":"","value":""}}
- add_recurring: data={{"type":"daily|monthly|day_of_week","text":"","time":"HH:MM","day":"מספר"}}
- delete_recurring: data={{"index":מספר}}

תאריכים:
- היום={now.strftime('%d/%m/%Y')}
- מחר={(now+timedelta(days=1)).strftime('%d/%m/%Y')}
- מחרתיים={(now+timedelta(days=2)).strftime('%d/%m/%Y')}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip().replace("```json","").replace("```","").strip()
    return json.loads(text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    add_to_history("משתמש", text)

    try:
        result = process_message(text)
        action = result.get("action", "chat")
        resp = result.get("response", "")
        data = result.get("data", {})

        meetings = load_meetings()
        notes = load_notes()
        memory = load_memory()
        recurring = load_recurring()
        now = datetime.now(ISRAEL_TZ)

        if action == "add_meeting":
            meeting = {
                "date": data["date"],
                "time": data["time"],
                "location": data.get("location", ""),
                "subject": data.get("subject", "פגישה"),
                "user_id": user_id,
                "reminded": False
            }
            meetings.append(meeting)
            save_meetings(meetings)
            resp = f"✅ *פגישה נוספה!*\n\n📅 תאריך: {meeting['date']}\n🕐 שעה: {meeting['time']}\n📋 נושא: {meeting['subject']}"
            if meeting["location"]:
                resp += f"\n📍 מיקום: {meeting['location']}"
            resp += "\n\n🔔 אזכיר לך שעתיים לפני!"

        elif action == "delete_meeting":
            idx = int(data.get("index", -1))
            if 0 <= idx < len(meetings):
                deleted = meetings.pop(idx)
                save_meetings(meetings)
                resp = f"🗑️ נמחקה: *{deleted['subject']}* ב-{deleted['date']} {deleted['time']}"
            else:
                resp = "❌ לא מצאתי. שאל 'מה הפגישות שלי?' לראות רשימה."

        elif action == "edit_meeting":
            idx = int(data.get("index", -1))
            field = data.get("field", "")
            value = data.get("value", "")
            if 0 <= idx < len(meetings) and field:
                old_val = meetings[idx].get(field, "")
                meetings[idx][field] = value
                if field in ["date", "time"]:
                    meetings[idx]["reminded"] = False
                save_meetings(meetings)
                resp = f"✅ עודכן!\n*{meetings[idx]['subject']}*\n{field}: {old_val} ➜ {value}"
            else:
                resp = "❌ לא הצלחתי לעדכן. נסה שוב."

        elif action == "list_today":
            today = now.strftime("%d/%m/%Y")
            filtered = [m for m in meetings if m["date"] == today]
            resp = format_meetings_list(filtered, f"📅 *לו\"ז להמשך היום ({today}):*")

        elif action == "list_tomorrow":
            tomorrow = (now + timedelta(days=1)).strftime("%d/%m/%Y")
            filtered = [m for m in meetings if m["date"] == tomorrow]
            resp = format_meetings_list(filtered, f"📅 *לו\"ז מחר ({tomorrow}):*")

        elif action == "list_week":
            dates = [(now + timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
            filtered = [m for m in meetings if m["date"] in dates]
            resp = format_meetings_list(filtered, "📅 *לו\"ז השבוע:*")

        elif action == "list_all":
            resp = format_meetings_list(meetings, "📅 *כל הפגישות:*")

        elif action == "add_note":
            note = {
                "text": data.get("text", text),
                "topic": data.get("topic", ""),
                "time": now.strftime("%d/%m/%Y %H:%M")
            }
            notes.append(note)
            save_notes(notes)
            resp = f"📝 *נשמר!*\n\n{note['text']}\n\n🕐 {note['time']}"

        elif action == "list_notes":
            topic = data.get("topic", "")
            filtered = [n for n in notes if not topic or topic.lower() in n["text"].lower() or topic.lower() in n.get("topic","").lower()]
            if not filtered:
                resp = "📝 אין פתקים."
            else:
                lines = ["📝 *פתקים:*", ""]
                for n in filtered:
                    lines.append(f"[{n['time']}] {n['text']}")
                resp = "\n".join(lines)

        elif action == "delete_note":
            idx = int(data.get("index", -1))
            if 0 <= idx < len(notes):
                notes.pop(idx)
                save_notes(notes)
                resp = "✅ הפתק נמחק!"

        elif action == "save_memory":
            key = data.get("key", "")
            value = data.get("value", "")
            if key:
                memory[key] = value
                save_memory(memory)
            resp = f"✅ זכרתי: {value}"

        elif action == "add_recurring":
            rec = {
                "type": data.get("type", "monthly"),
                "text": data.get("text", ""),
                "time": data.get("time", "09:00"),
                "day": str(data.get("day", "1")),
                "user_id": user_id
            }
            recurring.append(rec)
            save_recurring(recurring)
            resp = f"✅ *תזכורת חוזרת נוספה!*\n\n🔔 {rec['text']}\n⏰ {rec['type']} בשעה {rec['time']}"

        elif action == "list_recurring":
            if not recurring:
                resp = "אין תזכורות חוזרות."
            else:
                lines = ["🔔 *תזכורות חוזרות:*", ""]
                for i, r in enumerate(recurring):
                    lines.append(f"{i+1}. {r['text']} - {r['type']} בשעה {r.get('time','')}")
                resp = "\n".join(lines)

        elif action == "delete_recurring":
            idx = int(data.get("index", -1))
            if 0 <= idx < len(recurring):
                recurring.pop(idx)
                save_recurring(recurring)
                resp = "✅ התזכורת החוזרת נמחקה!"

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
        if meeting.get("reminded"):
            continue
        try:
            meeting_dt = datetime.strptime(f"{meeting['date']} {meeting['time']}", "%d/%m/%Y %H:%M")
            meeting_dt = ISRAEL_TZ.localize(meeting_dt)
            diff_minutes = (meeting_dt - now).total_seconds() / 60
            if 110 <= diff_minutes <= 130:
                msg = f"🔔 *תזכורת!*\n\nבעוד שעתיים:\n📋 *{meeting['subject']}*\n🕐 {meeting['time']}"
                if meeting.get("location"):
                    msg += f"\n📍 {meeting['location']}"
                await context.bot.send_message(chat_id=int(meeting["user_id"]), text=msg, parse_mode="Markdown")
                meeting["reminded"] = True
                changed = True
        except Exception as e:
            logger.error(f"Reminder error: {e}")
    if changed:
        save_meetings(meetings)

async def check_recurring(context: ContextTypes.DEFAULT_TYPE):
    recurring = load_recurring()
    now = datetime.now(ISRAEL_TZ)
    current_hm = now.strftime("%H:%M")
    for rec in recurring:
        if current_hm != rec.get("time", "09:00"):
            continue
        send = False
        rec_type = rec.get("type", "")
        rec_day = str(rec.get("day", "1"))
        if rec_type == "daily":
            send = True
        elif rec_type == "monthly" and str(now.day) == rec_day:
            send = True
        elif rec_type == "day_of_week":
            day_map = {"0": 6, "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5}
            if day_map.get(rec_day, -1) == now.weekday():
                send = True
        if send:
            try:
                await context.bot.send_message(chat_id=int(rec["user_id"]), text=f"🔔 *תזכורת:*\n{rec['text']}", parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Recurring error: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    jq = app.job_queue
    jq.run_daily(send_daily_schedule, time=datetime.strptime("21:00", "%H:%M").replace(tzinfo=ISRAEL_TZ).timetz())
    jq.run_daily(send_weekly_schedule, time=datetime.strptime("21:00", "%H:%M").replace(tzinfo=ISRAEL_TZ).timetz(), days=(5,))
    jq.run_repeating(send_reminders, interval=300, first=10)
    jq.run_repeating(check_recurring, interval=60, first=5)
    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
