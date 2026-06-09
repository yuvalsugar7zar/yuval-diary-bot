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

def add_to_history(role, text):
    history = load_history()
    history.append({
        "role": role,
        "text": text[:200],
        "time": datetime.now(ISRAEL_TZ).strftime("%d/%m/%Y %H:%M")
    })
    if len(history) > 200:
        history = history[-200:]
    save_history(history)

def get_history_text():
    history = load_history()
    if not history:
        return "אין היסטוריה."
    lines = []
    for h in history[-40:]:
        lines.append(f"[{h['time']}] {h['role']}: {h['text']}")
    return "\n".join(lines)

def get_meetings_text():
    meetings = load_meetings()
    if not meetings:
        return "אין פגישות."
    lines = []
    for i, m in enumerate(meetings):
        lines.append(f"{i}. {m['date']} {m['time']} - {m['subject']} | מיקום: {m.get('location','לא צוין')}")
    return "\n".join(lines)

def get_notes_text():
    notes = load_notes()
    if not notes:
        return "אין פתקים."
    lines = []
    for i, n in enumerate(notes):
        lines.append(f"{i}. [{n['time']}] {n['text']}")
    return "\n".join(lines)

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

היסטוריית שיחות:
{get_history_text()}

המשתמש כתב: "{user_text}"

ענה ב-JSON בלבד (ללא markdown):
{{
  "action": "add_meeting|delete_meeting|edit_meeting|list_today|list_tomorrow|list_week|list_all|add_note|list_notes|delete_note|save_memory|chat",
  "response": "תשובה בעברית למשתמש",
  "data": {{}}
}}

כללים לכל action:
- add_meeting: data={{"date":"DD/MM/YYYY","time":"HH:MM","location":"","subject":""}}
- delete_meeting: data={{"index":מספר}} (הפגישה האחרונה = מספר הפגישה האחרונה ברשימה)
- edit_meeting: data={{"index":מספר,"field":"time|location|subject|date","value":"ערך חדש"}}
- list_today/list_tomorrow/list_week/list_all: data={{}}
- add_note: data={{"text":"","topic":""}} (לעסקאות/הסכמות/מידע חשוב)
- list_notes: data={{"topic":"סינון לפי נושא או ריק"}}
- delete_note: data={{"index":מספר}}
- save_memory: data={{"key":"","value":""}} (לשמור שם/עיר/מידע אישי)
- chat: לכל שאלה אחרת, שימוש בהיסטוריה ובזיכרון לתשובה

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
    await update.message.reply_text("⏳ מעבד...")

    try:
        result = process_message(text)
        action = result.get("action", "chat")
        resp = result.get("response", "")
        data = result.get("data", {})

        meetings = load_meetings()
        notes = load_notes()
        memory = load_memory()
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

        elif action == "delete_meeting":
            idx = int(data.get("index", -1))
            if 0 <= idx < len(meetings):
                deleted = meetings.pop(idx)
                save_meetings(meetings)
                resp = f"🗑️ נמחקה: *{deleted['subject']}* ב-{deleted['date']} {deleted['time']}"
            else:
                resp = "❌ לא מצאתי את הפגישה. שאל 'מה הפגישות שלי?' כדי לראות את הרשימה."

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
                resp = f"✅ עודכן! *{meetings[idx]['subject']}*\n{field}: {old_val} ➜ {value}"
            else:
                resp = "❌ לא הצלחתי לעדכן. נסה שוב עם פרטים יותר ברורים."

        elif action == "list_today":
            today = now.strftime("%d/%m/%Y")
            filtered = [m for m in meetings if m["date"] == today]
            # גם רק פגישות עתידיות להיום
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
            else:
                resp = "❌ לא מצאתי את הפתק."

        elif action == "save_memory":
            key = data.get("key", "")
            value = data.get("value", "")
            if key:
                memory[key] = value
                save_memory(memory)

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
            logger.error(f"Daily schedule error: {e}")

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
            logger.error(f"Weekly schedule error: {e}")

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

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue = app.job_queue

    # לוז יומי ב-21:00
    job_queue.run_daily(
        send_daily_schedule,
        time=datetime.strptime("21:00", "%H:%M").replace(tzinfo=ISRAEL_TZ).timetz()
    )

    # לוז שבועי בשבת ב-09:00
    job_queue.run_daily(
        send_weekly_schedule,
        time=datetime.strptime("09:00", "%H:%M").replace(tzinfo=ISRAEL_TZ).timetz(),
        days=(5,)  # שבת
    )

    # תזכורות כל 5 דקות
    job_queue.run_repeating(send_reminders, interval=300, first=10)

    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()