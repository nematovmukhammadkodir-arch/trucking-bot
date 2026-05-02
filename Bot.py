"""
US Truck Drivers Platform — Telegram Bot MVP
=============================================
Установка:
  py -3.11 -m pip install python-telegram-bot==20.7
  py -3.11 -m pip install aiosqlite

Запуск:
  py -3.11 bot.py
"""

import logging
import asyncio
import re
from datetime import datetime, timedelta
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton,
    InlineKeyboardMarkup, Bot
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import aiosqlite

# ================================================================
#  ВСТАВЬ СВОИ ДАННЫЕ
# ================================================================
BOT_TOKEN = "8642506566:AAEa4zkFvpLmkPtTyPM2WRAAXdbRFndaGxQ"
ADMIN_ID  = 1262027571          # твой Telegram ID (от @userinfobot)
GROUP_ID  = -1003260001128     # ID группы (от @getidsbot)
# ================================================================

DB_FILE = "platform.db"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── BLACKLIST KEYWORDS ─────────────────────────────────────────
BLACKLIST = [
    "dispatch service", "dispatching service", "trailer rent",
    "trailer lease", "leasing program", "insurance ad",
    "crypto", "casino", "bitcoin", "invest", "forex",
    "make money fast", "earn from home", "referral link",
    "t.me/", "wa.me/", "whatsapp.com"
]

# ─── CHAT PERMISSIONS ───────────────────────────────────────────
PERM_NONE = ChatPermissions(can_send_messages=False)
PERM_READ_ONLY = ChatPermissions(can_send_messages=False)
PERM_FULL = ChatPermissions(
    can_send_messages=True,
    can_send_polls=True,
)

# ─── USER STATES ────────────────────────────────────────────────
STATE_JOIN_Q1      = "join_q1"       # CDL-A?
STATE_JOIN_Q2      = "join_q2"       # In US?
STATE_JOIN_Q3      = "join_q3"       # Role?
STATE_RULES        = "rules"         # Accept rules
STATE_NEW_MEMBER   = "new_member"    # Approved, not verified
STATE_VERIFY_START = "verify_start"  # Filling verify form
STATE_VERIFIED     = "verified"      # Fully verified driver
STATE_RESTRICTED   = "restricted"    # Restricted user
STATE_BANNED       = "banned"        # Banned

# ─── VERIFY FORM STEPS ──────────────────────────────────────────
VERIFY_STEPS = [
    ("full_name",   "👤 Full Name:"),
    ("phone",       "📞 Phone Number (US):"),
    ("experience",  "🚛 Years of CDL-A Experience:"),
    ("drive_type",  "👥 Solo or Team?\nReply: solo / team"),
    ("location",    "📍 Current State (e.g. Texas):"),
    ("lanes",       "🗺 Preferred lanes?\nReply: OTR / Regional / Local / Any"),
    ("cdl_photo",   "📸 Send a photo of your CDL license (front side)."),
    ("med_photo",   "📋 Now send a photo of your Medical Card."),
]
VERIFY_KEYS = [s[0] for s in VERIFY_STEPS]

# ─── JOB REQUEST FORM STEPS ─────────────────────────────────────
JOB_STEPS = [
    ("name",       "👤 Your Full Name:"),
    ("phone",      "📞 Phone Number:"),
    ("experience", "🚛 Years of Experience:"),
    ("drive_type", "👥 Solo or Team? (solo/team)"),
    ("state",      "📍 Current State:"),
    ("lanes",      "🗺 Preferred routes? (OTR / Regional / Local / Any)"),
]
JOB_KEYS = [s[0] for s in JOB_STEPS]


# ════════════════════════════════════════════════════════════════
#  DATABASE
# ════════════════════════════════════════════════════════════════

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                state       TEXT DEFAULT 'join_q1',
                role        TEXT DEFAULT '',
                warnings    INTEGER DEFAULT 0,
                joined_at   TEXT,
                verified_at TEXT,
                is_verified INTEGER DEFAULT 0,
                form_data   TEXT DEFAULT '{}'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                user_id     INTEGER PRIMARY KEY,
                full_name   TEXT,
                phone       TEXT,
                experience  TEXT,
                drive_type  TEXT,
                location    TEXT,
                lanes       TEXT,
                has_photo   INTEGER DEFAULT 0,
                cdl_photo   TEXT,
                submitted_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS job_requests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                name        TEXT,
                phone       TEXT,
                experience  TEXT,
                drive_type  TEXT,
                state       TEXT,
                lanes       TEXT,
                submitted_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                reported_id INTEGER,
                reason      TEXT,
                created_at  TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_log (
                user_id    INTEGER,
                timestamp  TEXT
            )
        """)
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ) as cursor:
            return await cursor.fetchone()


async def upsert_user(user_id, username, full_name, state=STATE_JOIN_Q1):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, state, joined_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name
        """, (user_id, username or "", full_name, state, datetime.now().isoformat()))
        await db.commit()


async def set_state(user_id, state):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET state=? WHERE user_id=?", (state, user_id)
        )
        await db.commit()


async def set_form_data(user_id, data: dict):
    import json
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET form_data=? WHERE user_id=?",
            (json.dumps(data), user_id)
        )
        await db.commit()


async def get_form_data(user_id) -> dict:
    import json
    user = await get_user(user_id)
    if user and user["form_data"]:
        try:
            return json.loads(user["form_data"])
        except Exception:
            return {}
    return {}


async def add_warning(user_id) -> int:
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET warnings = warnings + 1 WHERE user_id=?", (user_id,)
        )
        await db.commit()
        async with db.execute(
            "SELECT warnings FROM users WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 1


async def get_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE is_verified=1"
        ) as cur:
            verified = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE state='new_member'"
        ) as cur:
            new_members = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM job_requests") as cur:
            job_reqs = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM reports") as cur:
            reports = (await cur.fetchone())[0]
    return {
        "total": total,
        "verified": verified,
        "new_members": new_members,
        "job_requests": job_reqs,
        "reports": reports
    }


# ════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════

def is_admin(user_id):
    return user_id == ADMIN_ID


def check_blacklist(text: str) -> str | None:
    t = text.lower()
    for kw in BLACKLIST:
        if kw in t:
            return kw
    return None


async def flood_check(user_id: int) -> bool:
    """Returns True if user is flooding (5+ messages in 10 seconds)"""
    now = datetime.now()
    window = (now - timedelta(seconds=10)).isoformat()
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO message_log (user_id, timestamp) VALUES (?, ?)",
            (user_id, now.isoformat())
        )
        await db.execute(
            "DELETE FROM message_log WHERE timestamp < ?", (window,)
        )
        await db.commit()
        async with db.execute(
            "SELECT COUNT(*) FROM message_log WHERE user_id=? AND timestamp > ?",
            (user_id, window)
        ) as cur:
            count = (await cur.fetchone())[0]
    return count >= 5


def rules_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ I Accept the Rules", callback_data="accept_rules")
    ]])


def job_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Looking for a Job", callback_data="job_request")],
        [InlineKeyboardButton("📞 Contact Admin", callback_data="contact_admin")],
        [InlineKeyboardButton("🚨 Report a Problem", callback_data="report_menu")],
    ])


def report_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗣 Spam", callback_data="report_spam")],
        [InlineKeyboardButton("🎭 Fake Recruiter", callback_data="report_fake")],
        [InlineKeyboardButton("💸 Scam Offer", callback_data="report_scam")],
        [InlineKeyboardButton("😡 Disrespect", callback_data="report_disrespect")],
    ])


RULES_TEXT = """
📜 GROUP RULES — US CDL-A Drivers Platform

1. 🚛 Only CDL-A truck drivers allowed
2. 🇺🇸 Must be located in the United States
3. ❌ No ads, no spam, no self-promotion
4. ❌ No dispatch service ads, leasing, insurance spam
5. ❌ No links, forwards, or video reels
6. 🤝 Respect everyone — zero tolerance for insults
7. 💼 Job requests go through the bot only
8. 📢 Company offers posted by admin only
9. ⚠️ 3 violations = permanent ban

Press the button below to accept and get access.
"""


# ════════════════════════════════════════════════════════════════
#  NEW MEMBER JOINS GROUP
# ════════════════════════════════════════════════════════════════

async def new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue

        user_id = member.id
        username = member.username or ""
        full_name = member.full_name

        # Restrict immediately
        try:
            await context.bot.restrict_chat_member(
                chat_id=GROUP_ID, user_id=user_id, permissions=PERM_NONE
            )
        except Exception as e:
            logger.error(f"restrict error: {e}")

        # Save to DB
        await upsert_user(user_id, username, full_name, STATE_JOIN_Q1)

        # Start verification via DM
        try:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes", callback_data="cdl_yes"),
                 InlineKeyboardButton("❌ No",  callback_data="cdl_no")]
            ])
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"👋 Welcome to the US CDL-A Drivers Platform, {member.first_name}!\n\n"
                    "To get access to the group, I need to verify you.\n\n"
                    "❓ Do you have a CDL Class A license?"
                ),
                reply_markup=kb
            )
        except Exception:
            # User has DMs disabled
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"⚠️ New member can't receive DMs:\n"
                    f"{full_name} (@{username}) — ID: {user_id}\n"
                    f"Please verify manually."
                )
            )

        # Notify admin
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🔔 New member joined:\n"
                f"👤 {full_name} (@{username})\n"
                f"ID: {user_id}\n"
                f"Started verification..."
            )
        )

        try:
            await update.message.delete()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
#  CALLBACK QUERY HANDLER (all inline buttons)
# ════════════════════════════════════════════════════════════════

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    await query.answer()

    # ── JOIN FLOW: CDL question ──────────────────────────────────
    if data == "cdl_yes":
        await set_state(user_id, STATE_JOIN_Q2)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, I'm in the US", callback_data="us_yes"),
             InlineKeyboardButton("❌ No", callback_data="us_no")]
        ])
        await query.edit_message_text(
            "✅ Great!\n\n❓ Are you currently located in the United States?",
            reply_markup=kb
        )

    elif data == "cdl_no":
        await set_state(user_id, STATE_RESTRICTED)
        await query.edit_message_text(
            "❌ Sorry, this group is only for CDL Class A drivers.\n"
            "You will be removed from the group."
        )
        try:
            await context.bot.ban_chat_member(chat_id=GROUP_ID, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=GROUP_ID, user_id=user_id)
        except Exception:
            pass

    # ── JOIN FLOW: US location ───────────────────────────────────
    elif data == "us_yes":
        await set_state(user_id, STATE_JOIN_Q3)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚛 Driver",     callback_data="role_driver")],
            [InlineKeyboardButton("📋 Recruiter",  callback_data="role_recruiter")],
            [InlineKeyboardButton("📡 Dispatcher", callback_data="role_dispatcher")],
            [InlineKeyboardButton("❓ Other",       callback_data="role_other")],
        ])
        await query.edit_message_text(
            "✅ Perfect!\n\n❓ What is your role?",
            reply_markup=kb
        )

    elif data == "us_no":
        await set_state(user_id, STATE_RESTRICTED)
        await query.edit_message_text(
            "❌ Sorry, this group is for drivers located in the US only.\n"
            "You will be removed."
        )
        try:
            await context.bot.ban_chat_member(chat_id=GROUP_ID, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=GROUP_ID, user_id=user_id)
        except Exception:
            pass

    # ── JOIN FLOW: Role selection ────────────────────────────────
    elif data == "role_driver":
        # Driver → show rules
        await set_state(user_id, STATE_RULES)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE users SET role='driver' WHERE user_id=?", (user_id,)
            )
            await db.commit()
        await query.edit_message_text(RULES_TEXT, reply_markup=rules_keyboard())

    elif data in ("role_recruiter", "role_dispatcher", "role_other"):
        role = data.replace("role_", "")
        await set_state(user_id, STATE_RESTRICTED)
        await query.edit_message_text(
            f"⚠️ This group is for CDL-A drivers only.\n"
            f"Recruiters and dispatchers must be verified by the admin.\n"
            f"Contact admin if you have a legitimate reason to join."
        )
        # Notify admin
        user = await get_user(user_id)
        name = user["full_name"] if user else str(user_id)
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"⚠️ Non-driver tried to join:\n"
                f"👤 {name} (ID: {user_id})\n"
                f"Role: {role}\n\n"
                f"/approve_special {user_id} — allow anyway\n"
                f"/ban {user_id} — remove"
            )
        )

    # ── RULES ACCEPTANCE ────────────────────────────────────────
    elif data == "accept_rules":
        await set_state(user_id, STATE_NEW_MEMBER)
        # Grant read-only for 24h
        try:
            await context.bot.restrict_chat_member(
                chat_id=GROUP_ID, user_id=user_id, permissions=PERM_NONE
            )
        except Exception:
            pass

        await query.edit_message_text(
            "✅ Rules accepted!\n\n"
            "You now have access to the group.\n\n"
            "📋 To get full posting access, complete your driver verification.\n"
            "Use the button below or type /verify"
        )
        await context.bot.send_message(
            chat_id=user_id,
            text="What would you like to do?",
            reply_markup=job_menu_keyboard()
        )
        # Notify admin
        user = await get_user(user_id)
        name = user["full_name"] if user else str(user_id)
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"✅ New member accepted rules:\n"
                f"👤 {name} (ID: {user_id})\n"
                f"Awaiting verification."
            )
        )

    # ── JOB REQUEST ─────────────────────────────────────────────
    elif data == "job_request":
        await set_state(user_id, "job_step_0")
        await set_form_data(user_id, {})
        await query.edit_message_text(
            f"📋 Job Request Form\n\n"
            f"Question 1 of {len(JOB_STEPS)}:\n\n"
            f"{JOB_STEPS[0][1]}"
        )

    elif data == "contact_admin":
        await query.edit_message_text(
            "📞 To contact the admin directly, send your message here "
            "and it will be forwarded."
        )
        await set_state(user_id, "contact_admin")

    # ── REPORT ──────────────────────────────────────────────────
    elif data == "report_menu":
        await query.edit_message_text(
            "🚨 What would you like to report?",
            reply_markup=report_keyboard()
        )

    elif data.startswith("report_"):
        reason_map = {
            "report_spam": "Spam",
            "report_fake": "Fake Recruiter",
            "report_scam": "Scam Offer",
            "report_disrespect": "Disrespect"
        }
        reason = reason_map.get(data, "Other")
        user = await get_user(user_id)
        name = user["full_name"] if user else str(user_id)

        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT INTO reports (reporter_id, reported_id, reason, created_at) VALUES (?,?,?,?)",
                (user_id, 0, reason, datetime.now().isoformat())
            )
            await db.commit()

        await query.edit_message_text(
            f"✅ Report submitted: {reason}\n"
            "The admin will review it. Thank you!"
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🚨 NEW REPORT\n"
                f"From: {name} (ID: {user_id})\n"
                f"Reason: {reason}\n"
                f"Time: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        )

    # ── VERIFY START (from button) ───────────────────────────────
    elif data == "start_verify":
        await set_state(user_id, "verify_step_0")
        await set_form_data(user_id, {})
        await query.edit_message_text(
            f"📋 Driver Verification Form\n\n"
            f"Step 1 of {len(VERIFY_STEPS)}:\n\n"
            f"{VERIFY_STEPS[0][1]}"
        )


# ════════════════════════════════════════════════════════════════
#  PRIVATE MESSAGE HANDLER (form filling)
# ════════════════════════════════════════════════════════════════

async def handle_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if is_admin(user_id):
        return

    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Please join our group first, then start verification.")
        return

    state = user["state"]

    # ── VERIFY FORM ──────────────────────────────────────────────
    if state.startswith("verify_step_"):
        step = int(state.split("_")[-1])
        key = VERIFY_KEYS[step]
        form = await get_form_data(user_id)

        # Last step = photo, but user typed text
        if step == len(VERIFY_STEPS) - 1:
            if text.lower() in ["skip", "пропустить", "no", "-"]:
                form[key] = "not provided"
                await set_form_data(user_id, form)
                await finish_verify(update, context, user_id, form)
            else:
                await update.message.reply_text(
                    "📸 Please send a photo of your CDL, or type: skip"
                )
            return

        form[key] = text
        next_step = step + 1
        await set_form_data(user_id, form)
        await set_state(user_id, f"verify_step_{next_step}")

        if next_step < len(VERIFY_STEPS):
            await update.message.reply_text(
                f"Step {next_step + 1} of {len(VERIFY_STEPS)}:\n\n"
                f"{VERIFY_STEPS[next_step][1]}"
            )
        else:
            await finish_verify(update, context, user_id, form)

    # ── JOB REQUEST FORM ────────────────────────────────────────
    elif state.startswith("job_step_"):
        step = int(state.split("_")[-1])
        key = JOB_KEYS[step]
        form = await get_form_data(user_id)
        form[key] = text
        next_step = step + 1
        await set_form_data(user_id, form)

        if next_step < len(JOB_STEPS):
            await set_state(user_id, f"job_step_{next_step}")
            await update.message.reply_text(
                f"Question {next_step + 1} of {len(JOB_STEPS)}:\n\n"
                f"{JOB_STEPS[next_step][1]}"
            )
        else:
            await finish_job_request(update, context, user_id, form)

    # ── CONTACT ADMIN ────────────────────────────────────────────
    elif state == "contact_admin":
        name = user["full_name"]
        username = user["username"]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📩 Message from driver:\n"
                f"👤 {name} (@{username}) — ID: {user_id}\n\n"
                f"{text}"
            )
        )
        await update.message.reply_text(
            "✅ Message sent to admin. They will contact you soon."
        )
        await set_state(user_id, STATE_NEW_MEMBER)

    # ── DEFAULT ──────────────────────────────────────────────────
    else:
        await update.message.reply_text(
            "Use the menu below to get started:",
            reply_markup=job_menu_keyboard()
        )


async def handle_private_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        return

    state = user["state"]
    if not state.startswith("verify_step_"):
        return

    step = int(state.split("_")[-1])

    # CDL photo — step 6
    if step == 6:
        photo = update.message.photo[-1]
        form = await get_form_data(user_id)
        form["cdl_photo_file_id"] = photo.file_id
        await set_form_data(user_id, form)
        await set_state(user_id, "verify_step_7")
        await update.message.reply_text(
            f"✅ CDL photo received!\n\n"
            f"Step 8 of {len(VERIFY_STEPS)}:\n\n"
            f"{VERIFY_STEPS[7][1]}"
        )

    # Medical card photo — step 7 (last)
    elif step == 7:
        photo = update.message.photo[-1]
        form = await get_form_data(user_id)
        form["med_photo_file_id"] = photo.file_id
        await set_form_data(user_id, form)
        await update.message.reply_text("✅ Medical card photo received!")
        await finish_verify(update, context, user_id, form)

    else:
        await update.message.reply_text(
            f"Please answer step {step + 1} first:\n\n"
            f"{VERIFY_STEPS[step][1]}"
        )


# ════════════════════════════════════════════════════════════════
#  FORM COMPLETION
# ════════════════════════════════════════════════════════════════

async def finish_verify(update, context, user_id, form):
    user = await get_user(user_id)
    tg_name = user["full_name"] if user else str(user_id)
    tg_user = user["username"] if user else ""
    has_photo = bool(form.get("photo_file_id"))
    photo_status = "✅ provided" if has_photo else "❌ not provided"

    # Save to drivers table
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT OR REPLACE INTO drivers
            (user_id, full_name, phone, experience, drive_type, location, lanes, has_photo, cdl_photo, submitted_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            user_id,
            form.get("full_name", tg_name),
            form.get("phone", "—"),
            form.get("experience", "—"),
            form.get("drive_type", "—"),
            form.get("location", "—"),
            form.get("lanes", "—"),
            1 if has_photo else 0,
            form.get("photo_file_id", ""),
            datetime.now().isoformat()
        ))
        await db.commit()

    await set_state(user_id, "pending_verify")

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "✅ Verification form submitted!\n\n"
            "The admin will review it and approve you shortly.\n"
            "You'll be notified once approved. 🚛"
        )
    )

    # Send to admin
    anketa = (
        f"📋 DRIVER VERIFICATION REQUEST\n"
        f"{'═' * 34}\n"
        f"👤 Name:        {form.get('full_name', tg_name)}\n"
        f"📞 Phone:       {form.get('phone', '—')}\n"
        f"🚛 Experience:  {form.get('experience', '—')} years\n"
        f"👥 Drive type:  {form.get('drive_type', '—')}\n"
        f"📍 Location:    {form.get('location', '—')}\n"
        f"🗺 Lanes:       {form.get('lanes', '—')}\n"
        f"📸 CDL Photo:   {photo_status}\n"
        f"{'─' * 34}\n"
        f"💬 Telegram:    {tg_name} (@{tg_user})\n"
        f"🆔 ID:          {user_id}\n"
        f"🕐 Date:        {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"{'═' * 34}\n\n"
        f"/verify_approve {user_id} — ✅ Approve\n"
        f"/verify_reject {user_id}  — 🚫 Reject"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=anketa)

    # CDL photo
    if form.get("cdl_photo_file_id"):
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=form["cdl_photo_file_id"],
            caption=f"📸 CDL License — {form.get('full_name', tg_name)} | ID: {user_id}"
        )

    # Medical card photo
    if form.get("med_photo_file_id"):
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=form["med_photo_file_id"],
            caption=f"📋 Medical Card — {form.get('full_name', tg_name)} | ID: {user_id}"
        )


async def finish_job_request(update, context, user_id, form):
    user = await get_user(user_id)
    tg_name = user["full_name"] if user else str(user_id)
    tg_user = user["username"] if user else ""

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO job_requests
            (user_id, name, phone, experience, drive_type, state, lanes, submitted_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            user_id,
            form.get("name", tg_name),
            form.get("phone", "—"),
            form.get("experience", "—"),
            form.get("drive_type", "—"),
            form.get("state", "—"),
            form.get("lanes", "—"),
            datetime.now().isoformat()
        ))
        await db.commit()

    await set_state(user_id, STATE_NEW_MEMBER)

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "✅ Job request submitted!\n\n"
            "The HR manager will contact you within 24 hours. 🚛"
        )
    )

    job_text = (
        f"💼 NEW JOB REQUEST\n"
        f"{'═' * 30}\n"
        f"👤 Name:       {form.get('name', tg_name)}\n"
        f"📞 Phone:      {form.get('phone', '—')}\n"
        f"🚛 Experience: {form.get('experience', '—')} years\n"
        f"👥 Type:       {form.get('drive_type', '—')}\n"
        f"📍 State:      {form.get('state', '—')}\n"
        f"🗺 Lanes:      {form.get('lanes', '—')}\n"
        f"{'─' * 30}\n"
        f"💬 Telegram:   {tg_name} (@{tg_user})\n"
        f"🆔 ID:         {user_id}\n"
        f"🕐 Date:       {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"{'═' * 30}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=job_text)


# ════════════════════════════════════════════════════════════════
#  GROUP MESSAGE MODERATION
# ════════════════════════════════════════════════════════════════

async def moderate_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user_id = update.effective_user.id
    if is_admin(user_id):
        return

    user = await get_user(user_id)
    if not user:
        return

    state = user["state"]
    msg = update.message

    # Only verified drivers can post
    if state != STATE_VERIFIED:
        try:
            await msg.delete()
        except Exception:
            pass
        return

    text = msg.text or msg.caption or ""

    # Block forwards
    if msg.forward_date:
        await msg.delete()
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"@{update.effective_user.username or update.effective_user.first_name} — ❌ Forwarded messages are not allowed."
        )
        return

    # Block links
    if msg.entities:
        for entity in msg.entities:
            if entity.type in ("url", "text_link"):
                await msg.delete()
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    text=f"@{update.effective_user.username or update.effective_user.first_name} — ❌ Links are not allowed in this group."
                )
                return

    # Blacklist keywords
    hit = check_blacklist(text)
    if hit:
        warnings = await add_warning(user_id)
        try:
            await msg.delete()
        except Exception:
            pass

        if warnings == 1:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"⚠️ @{update.effective_user.username or update.effective_user.first_name} — Warning 1/3: Prohibited content detected."
            )
        elif warnings == 2:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"⚠️ @{update.effective_user.username or update.effective_user.first_name} — Warning 2/3: Next violation = 24h mute."
            )
        elif warnings == 3:
            # Mute 24h
            until = datetime.now() + timedelta(hours=24)
            try:
                await context.bot.restrict_chat_member(
                    chat_id=GROUP_ID, user_id=user_id,
                    permissions=PERM_NONE, until_date=until
                )
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"🔇 @{update.effective_user.username or update.effective_user.first_name} — Muted 24 hours (3 violations)."
            )
        else:
            # Ban
            try:
                await context.bot.ban_chat_member(chat_id=GROUP_ID, user_id=user_id)
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"🚫 User banned after repeated violations."
            )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🚨 Violation by {user['full_name']} (ID:{user_id})\nKeyword: '{hit}'\nWarnings: {warnings}\nMessage: {text[:200]}"
        )
        return

    # Flood check
    if await flood_check(user_id):
        try:
            await msg.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"⚠️ @{update.effective_user.username or update.effective_user.first_name} — Slow down! Too many messages."
        )


# ════════════════════════════════════════════════════════════════
#  ADMIN COMMANDS
# ════════════════════════════════════════════════════════════════

async def cmd_verify_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /verify_approve <user_id>")
        return

    user_id = int(context.args[0])

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET state=?, is_verified=1, verified_at=? WHERE user_id=?",
            (STATE_VERIFIED, datetime.now().isoformat(), user_id)
        )
        await db.commit()

    try:
        await context.bot.restrict_chat_member(
            chat_id=GROUP_ID, user_id=user_id, permissions=PERM_FULL
        )
    except Exception as e:
        logger.error(e)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ You are now a VERIFIED DRIVER! 🚛\n\n"
                "You can now post in the group.\n"
                "Stay professional and follow the rules.\n\n"
                "Welcome to the platform! 💪"
            )
        )
    except Exception:
        pass

    await update.message.reply_text(f"✅ Driver {user_id} verified and approved.")


async def cmd_verify_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /verify_reject <user_id> [reason]")
        return

    user_id = int(context.args[0])
    reason = " ".join(context.args[1:]) or "not specified"

    await set_state(user_id, STATE_NEW_MEMBER)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"❌ Verification not approved.\n"
                f"Reason: {reason}\n\n"
                f"Contact admin if you have questions."
            )
        )
    except Exception:
        pass

    await update.message.reply_text(f"🚫 Verification rejected for {user_id}.")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick approve — give posting rights without full verify"""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return
    user_id = int(context.args[0])
    await set_state(user_id, STATE_NEW_MEMBER)
    try:
        await context.bot.restrict_chat_member(
            chat_id=GROUP_ID, user_id=user_id, permissions=PERM_NONE
        )
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ You've been approved! Complete /verify to get full posting access."
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return
    await update.message.reply_text(f"✅ User {user_id} approved (read-only access).")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /reject <user_id> [reason]")
        return
    user_id = int(context.args[0])
    reason = " ".join(context.args[1:]) or "not specified"
    try:
        await context.bot.ban_chat_member(chat_id=GROUP_ID, user_id=user_id)
        await context.bot.unban_chat_member(chat_id=GROUP_ID, user_id=user_id)
        await context.bot.send_message(
            chat_id=user_id,
            text=f"You have been removed from the group.\nReason: {reason}"
        )
    except Exception as e:
        logger.error(e)
    await set_state(user_id, STATE_BANNED)
    await update.message.reply_text(f"🚫 User {user_id} rejected.")


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /warn @username reason")
        return
    username = context.args[0].lstrip("@")
    reason = " ".join(context.args[1:])
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=f"⚠️ @{username} — Warning from admin: {reason}"
    )
    await update.message.reply_text("Warning sent.")


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /mute <user_id> [hours]")
        return
    user_id = int(context.args[0])
    hours = int(context.args[1]) if len(context.args) > 1 else 24
    until = datetime.now() + timedelta(hours=hours)
    try:
        await context.bot.restrict_chat_member(
            chat_id=GROUP_ID, user_id=user_id,
            permissions=PERM_NONE, until_date=until
        )
        await update.message.reply_text(f"🔇 Muted for {hours}h.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id>")
        return
    user_id = int(context.args[0])
    try:
        await context.bot.ban_chat_member(chat_id=GROUP_ID, user_id=user_id)
        await set_state(user_id, STATE_BANNED)
        await update.message.reply_text(f"🚫 User {user_id} banned.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    user_id = int(context.args[0])
    try:
        await context.bot.unban_chat_member(chat_id=GROUP_ID, user_id=user_id)
        await update.message.reply_text(f"✅ User {user_id} unbanned.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = await get_stats()
    await update.message.reply_text(
        f"📊 PLATFORM STATS\n"
        f"{'─' * 25}\n"
        f"👥 Total users:      {s['total']}\n"
        f"✅ Verified drivers: {s['verified']}\n"
        f"🆕 New members:      {s['new_members']}\n"
        f"💼 Job requests:     {s['job_requests']}\n"
        f"🚨 Reports:          {s['reports']}\n"
        f"{'─' * 25}\n"
        f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )


async def cmd_drivers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM drivers ORDER BY submitted_at DESC") as cur:
            rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("No verified drivers yet.")
        return
    lines = [f"✅ Verified Drivers ({len(rows)}):"]
    for r in rows:
        lines.append(
            f"• {r['full_name']} | 📞 {r['phone']} | "
            f"CDL-A {r['experience']}yr | {r['location']} | {r['lanes']}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM job_requests ORDER BY submitted_at DESC LIMIT 20"
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("No job requests yet.")
        return
    lines = [f"💼 Job Requests ({len(rows)}):"]
    for r in rows:
        lines.append(
            f"• {r['name']} | 📞 {r['phone']} | "
            f"{r['experience']}yr | {r['state']} | {r['lanes']}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast your message here")
        return
    text = " ".join(context.args)
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE state IN (?,?)",
            (STATE_VERIFIED, STATE_NEW_MEMBER)
        ) as cur:
            rows = await cur.fetchall()
    sent = failed = 0
    for row in rows:
        try:
            await context.bot.send_message(
                chat_id=row[0],
                text=f"📢 Message from HR Admin:\n\n{text}"
            )
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"Broadcast done: ✅ {sent} sent, ❌ {failed} failed.")


async def cmd_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post a company offer to the group — admin only"""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /offer\n"
            "Company: \n"
            "Pay (CPM/Weekly): \n"
            "Equipment: \n"
            "Route: OTR/Regional/Local\n"
            "Home Time: \n"
            "Location: \n"
            "Tags: #solo #team #otr"
        )
        return
    offer_text = " ".join(context.args)
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=(
            f"📢 COMPANY OFFER\n"
            f"{'═' * 30}\n"
            f"{offer_text}\n"
            f"{'═' * 30}\n"
            f"📩 Interested? Contact admin directly."
        )
    )
    await update.message.reply_text("✅ Offer posted to group.")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        await update.message.reply_text(
            "👋 Admin panel:\n\n"
            "/stats — platform statistics\n"
            "/drivers — verified drivers list\n"
            "/leads — job requests\n"
            "/verify_approve ID — approve verification\n"
            "/verify_reject ID — reject verification\n"
            "/approve ID — quick approve\n"
            "/reject ID — remove user\n"
            "/warn @user reason — warn\n"
            "/mute ID hours — mute\n"
            "/ban ID — ban\n"
            "/unban ID — unban\n"
            "/broadcast text — message all\n"
            "/offer text — post company offer"
        )
        return

    user = await get_user(user_id)
    if user and user["state"] in (STATE_VERIFIED, STATE_NEW_MEMBER):
        await update.message.reply_text(
            "Welcome back! What can I help you with?",
            reply_markup=job_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "Join our Telegram group first to use this bot. 🚛"
        )


async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("Please join our group first.")
        return
    if user["is_verified"]:
        await update.message.reply_text("✅ You are already a Verified Driver!")
        return

    await set_state(user_id, "verify_step_0")
    await set_form_data(user_id, {})
    await update.message.reply_text(
        f"📋 Driver Verification Form\n\n"
        f"Step 1 of {len(VERIFY_STEPS)}:\n\n"
        f"{VERIFY_STEPS[0][1]}"
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "What would you like to do?",
        reply_markup=job_menu_keyboard()
    )


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

async def post_init(app):
    await init_db()
    logger.info("Database initialized.")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Admin commands
    app.add_handler(CommandHandler("start",           cmd_start))
    app.add_handler(CommandHandler("verify",          cmd_verify))
    app.add_handler(CommandHandler("menu",            cmd_menu))
    app.add_handler(CommandHandler("stats",           cmd_stats))
    app.add_handler(CommandHandler("drivers",         cmd_drivers))
    app.add_handler(CommandHandler("leads",           cmd_leads))
    app.add_handler(CommandHandler("verify_approve",  cmd_verify_approve))
    app.add_handler(CommandHandler("verify_reject",   cmd_verify_reject))
    app.add_handler(CommandHandler("approve",         cmd_approve))
    app.add_handler(CommandHandler("reject",          cmd_reject))
    app.add_handler(CommandHandler("warn",            cmd_warn))
    app.add_handler(CommandHandler("mute",            cmd_mute))
    app.add_handler(CommandHandler("ban",             cmd_ban))
    app.add_handler(CommandHandler("unban",           cmd_unban))
    app.add_handler(CommandHandler("broadcast",       cmd_broadcast))
    app.add_handler(CommandHandler("offer",           cmd_offer))

    # New member in group
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member
    ))

    # Group moderation
    app.add_handler(MessageHandler(
        filters.Chat(GROUP_ID) & ~filters.COMMAND, moderate_group
    ))

    # Private: photos (CDL)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.PHOTO, handle_private_photo
    ))

    # Private: text (forms)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        handle_private_text
    ))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("✅ Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()