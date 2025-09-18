
import asyncio
import csv
import io
import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ===================== CONFIG =====================
BOT_TOKEN = "bot token"  # <-- replace with your real token
OWNER_ID = 'your id'  # <-- replace with your own Telegram numeric user ID
DB_PATH = "/feedback2.db"

# Conversation states
WAITING_COMMENT = 1

# ===================== LOGGING =====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ===================== DATABASE =====================

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_db():
    with closing(get_db_connection()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                last_name  TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                username   TEXT,
                rating     INTEGER CHECK (rating BETWEEN 0 AND 5),
                comment    TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            """
        )

# ===================== SCHEMA MIGRATION HELPERS =====================

def _add_col_if_missing(conn, table: str, col: str, decl: str):
    cur = conn.execute(f"PRAGMA table_info({table});")
    cols = {row[1] for row in cur.fetchall()}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")

def migrate_schema():
    with closing(get_db_connection()) as conn, conn:
        _add_col_if_missing(conn, "users", "username",   "TEXT")
        _add_col_if_missing(conn, "users", "first_name", "TEXT")
        _add_col_if_missing(conn, "users", "last_name",  "TEXT")
        _add_col_if_missing(conn, "users", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")
        _add_col_if_missing(conn, "feedback", "username",   "TEXT")
        _add_col_if_missing(conn, "feedback", "comment",    "TEXT")
        _add_col_if_missing(conn, "feedback", "created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")

# ===================== BOT DB OPS =====================

def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str]):
    with closing(get_db_connection()) as conn, conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                updated_at=CURRENT_TIMESTAMP;
            """,
            (user_id, username, first_name, last_name),
        )


def save_rating(user_id: int, username: Optional[str], rating: int, comment: Optional[str] = None):
    with closing(get_db_connection()) as conn, conn:
        conn.execute(
            """
            INSERT INTO feedback (user_id, username, rating, comment)
            VALUES (?, ?, ?, ?);
            """,
            (user_id, username, rating, comment),
        )


def fetch_user_ratings(user_id: int, limit: int = 10):
    with closing(get_db_connection()) as conn, conn:
        cur = conn.execute(
            """
            SELECT rating, IFNULL(comment, ''), created_at
            FROM feedback
            WHERE user_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?;
            """,
            (user_id, limit),
        )
        return cur.fetchall()


def export_all_feedback_csv() -> io.BytesIO:
    with closing(get_db_connection()) as conn, conn:
        cur = conn.execute(
            """
            SELECT id, user_id, username, rating, comment, created_at
            FROM feedback
            ORDER BY id ASC;
            """
        )
        rows = cur.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "user_id", "username", "rating", "comment", "created_at"])
    for r in rows:
        writer.writerow(r)

    bio = io.BytesIO(output.getvalue().encode("utf-8"))
    bio.name = f"feedback_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return bio

# ===================== UI HELPERS =====================

def rating_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=str(n), callback_data=f"rate:{n}") for n in range(0, 6)]]
    return InlineKeyboardMarkup(buttons)

# ===================== HANDLERS =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    msg = (
        "👋 Ciao! Use /rate to rate the visual style from 0 to 5.\n"
        "After choosing a rating, you may add an optional comment or /skip.\n\n"
        "Commands:\n"
        "• /rate — start a rating flow\n"
        "• /myratings — see your recent ratings\n"
        "• /export — owner only, download all feedback as CSV\n"
    )
    await update.message.reply_text(msg)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def rate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    await update.message.reply_text("Tap a rating (0–5):", reply_markup=rating_keyboard())
    return WAITING_COMMENT

async def on_button_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, rating_str = query.data.split(":", 1)
        rating = int(rating_str)
    except Exception:
        await query.edit_message_text("Oops, malformed data.")
        return ConversationHandler.END
    user = update.effective_user
    save_rating(user.id, user.username, rating)
    confirm = f"✅ Saved rating {rating}/5.\nYou can now send an optional short comment (under 200 chars), or /skip."
    try:
        await query.edit_message_text(text=f"Your rating: {rating}/5")
    except Exception:
        pass
    await query.message.reply_text(confirm)
    context.user_data["current_rating"] = rating
    return WAITING_COMMENT

async def on_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rating = context.user_data.get("current_rating")
    if rating is None:
        await update.message.reply_text("No active rating. Use /rate.")
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if len(text) > 200:
        await update.message.reply_text("Please keep the comment under 200 characters, or /skip.")
        return WAITING_COMMENT
    user = update.effective_user
    save_rating(user.id, user.username, rating, comment=text)
    await update.message.reply_text("📝 Comment saved. Thanks!")
    context.user_data.pop("current_rating", None)
    return ConversationHandler.END

async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("current_rating", None)
    await update.message.reply_text("No comment recorded. Thanks for your rating!")
    return ConversationHandler.END

async def my_ratings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = fetch_user_ratings(user.id, limit=10)
    if not rows:
        await update.message.reply_text("You have no ratings yet. Use /rate to start.")
        return
    lines = ["Your latest ratings:"]
    for r, c, when in rows:
        short_c = (c[:40] + "…") if c and len(c) > 43 else c
        lines.append(f"• {when} — ⭐ {r}/5" + (f", \"{short_c}\"" if short_c else ""))
    await update.message.reply_text("\n".join(lines))

async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Only the owner can export.")
        return
    bio = export_all_feedback_csv()
    await update.message.reply_document(document=InputFile(bio), caption="All feedback (CSV)")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Unknown command. Try /start.")

# ===================== MAIN =====================

def main():
    ensure_db()
    migrate_schema()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("rate", rate_cmd), CallbackQueryHandler(on_button_rate, pattern=r"^rate:.*")],
        states={WAITING_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_comment), CommandHandler("skip", skip_comment)]},
        fallbacks=[CommandHandler("skip", skip_comment)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myratings", my_ratings))
    app.add_handler(CommandHandler("export", export_csv))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    logger.info("Bot is starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
