import asyncio
import csv
import hmac
import hashlib
import io
import logging
import random
import string
import sys
from datetime import datetime, timedelta, timezone
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("TelegramStoreBot")

# Global Instances
bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Pricing Constants
STAR_PRICE_PER_UNIT = 0.022
STAR_SELL_RATE = 0.015

# FSM States
class DepositStates(StatesGroup):
    waiting_for_amount = State()

class CustomStarsStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_recipient = State()

class PremiumStates(StatesGroup):
    waiting_for_recipient = State()

class AdvancedGiveawayStates(StatesGroup):
    waiting_for_prize_type = State()
    waiting_for_total_amount = State()
    waiting_for_winner_count = State()
    waiting_for_duration = State()
    waiting_for_host = State()
    waiting_for_channels = State()

# Database Initialization
async def init_db():
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0.0,
                total_spent REAL DEFAULT 0.0,
                total_deposited REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
                product_type TEXT,
                product_id TEXT,
                amount REAL,
                status TEXT,
                recipient TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                giveaway_id TEXT PRIMARY KEY,
                creator_id INTEGER,
                prize_type TEXT,
                total_amount INTEGER,
                winner_count INTEGER,
                host_info TEXT,
                required_channels TEXT,
                end_time TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_participants (
                giveaway_id TEXT,
                user_id INTEGER,
                username TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (giveaway_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS forced_winners (
                giveaway_id TEXT,
                user_id INTEGER,
                PRIMARY KEY (giveaway_id, user_id)
            )
        """)
        await db.commit()
    logger.info("Database and Giveaway tables initialized.")

# Helper Functions
def generate_id(prefix="GA") -> str:
    rand_str = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{rand_str}"

async def get_or_create_user(user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT user_id, balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute("INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)", (user_id, username, first_name))
                await db.commit()
                return {"user_id": user_id, "balance": 0.0}
            return {"user_id": user[0], "balance": user[1]}

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Buy Premium", callback_data="nav_buy_premium", style="primary"),
                InlineKeyboardButton(text="⭐ Buy Stars", callback_data="nav_buy_stars", style="success"),
            ],
            [
                InlineKeyboardButton(text="🎁 Create Giveaway", callback_data="nav_create_giveaway", style="success"),
                InlineKeyboardButton(text="👛 Wallet", callback_data="nav_wallet", style="primary"),
            ],
        ]
    )

# --- START HANDLER ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_data = await get_or_create_user(message.from_user.id, message.from_user.username or "", message.from_user.first_name or "")
    text = (
        f"🌌 **WELCOME TO AUTOMATED STORE & GIVEAWAYS**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 **Balance:** `${user_data['balance']:.2f} USD`\n"
        f"👤 **User ID:** `{message.from_user.id}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        "Choose an option from below:"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# --- ADVANCED GIVEAWAY CREATION WIZARD ---

@router.callback_query(F.data == "nav_create_giveaway")
async def cb_start_giveaway(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdvancedGiveawayStates.waiting_for_prize_type)
    text = (
        "🎁 **CREATE CUSTOM GIVEAWAY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Step 1: Select Prize Type for the Giveaway:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="ga_prize_stars", style="success"),
            InlineKeyboardButton(text="💎 Telegram Premium", callback_data="ga_prize_premium", style="primary")
        ],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(AdvancedGiveawayStates.waiting_for_prize_type)
async def process_ga_prize_type(callback: CallbackQuery, state: FSMContext):
    prize_type = "stars" if callback.data == "ga_prize_stars" else "premium"
    await state.update_data(prize_type=prize_type)
    await state.set_state(AdvancedGiveawayStates.waiting_for_total_amount)
    
    unit = "Stars" if prize_type == "stars" else "Premium Months/Subscriptions"
    await callback.message.edit_text(
        f"🎁 **STEP 2: TOTAL AMOUNT**\n\nHow many total {unit} are you giving away?\n*(Example: Send 100 for 100 Stars or 5 for 5 Premium Subs)*",
        parse_mode="Markdown"
    )

@router.message(AdvancedGiveawayStates.waiting_for_total_amount)
async def process_ga_total_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid number greater than 0.")
        return

    await state.update_data(total_amount=amount)
    await state.set_state(AdvancedGiveawayStates.waiting_for_winner_count)
    await message.answer("👥 **STEP 3: NUMBER OF WINNERS**\n\nHow many total winners should be selected?\n*(Example: 5 winners will split the prize)*")

@router.message(AdvancedGiveawayStates.waiting_for_winner_count)
async def process_ga_winner_count(message: Message, state: FSMContext):
    try:
        winners = int(message.text.strip())
        if winners <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid winner count.")
        return

    await state.update_data(winner_count=winners)
    await state.set_state(AdvancedGiveawayStates.waiting_for_duration)
    await message.answer("⏰ **STEP 4: DURATION**\n\nEnter duration in hours or days (e.g., `12h` for 12 hours or `3d` for 3 days):")

@router.message(AdvancedGiveawayStates.waiting_for_duration)
async def process_ga_duration(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    hours = 0
    if text.endswith("h"):
        hours = int(text.replace("h", ""))
    elif text.endswith("d"):
        hours = int(text.replace("d", "")) * 24
    else:
        try:
            hours = int(text)
        except ValueError:
            await message.answer("⚠️ Invalid format! Use `12h` or `2d`.")
            return

    end_time = datetime.now(timezone.utc) + timedelta(hours=hours)
    await state.update_data(end_time=end_time.isoformat())
    await state.set_state(AdvancedGiveawayStates.waiting_for_host)
    await message.answer("👑 **STEP 5: HOST INFO**\n\nWho is holding this giveaway?\n*(Send Host Name or Channel Name, e.g., @MyAwesomeChannel)*")

@router.message(AdvancedGiveawayStates.waiting_for_host)
async def process_ga_host(message: Message, state: FSMContext):
    await state.update_data(host_info=message.text.strip())
    await state.set_state(AdvancedGiveawayStates.waiting_for_channels)
    await message.answer("📢 **STEP 6: REQUIRED CHANNELS**\n\nSend required channel `@usernames` separated by space (or send `none` if no channels required):")

@router.message(AdvancedGiveawayStates.waiting_for_channels)
async def process_ga_channels(message: Message, state: FSMContext):
    channels = message.text.strip()
    if channels.lower() == "none":
        channels = ""
        
    data = await state.get_data()
    ga_id = generate_id("GA")
    
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("""
            INSERT INTO giveaways (giveaway_id, creator_id, prize_type, total_amount, winner_count, host_info, required_channels, end_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ga_id, message.from_user.id, data["prize_type"], data["total_amount"], data["winner_count"], data["host_info"], channels, data["end_time"]))
        await db.commit()

    await state.clear()
    
    # Broadcast / Publish Preview
    per_winner = data["total_amount"] // data["winner_count"]
    unit_str = "Stars ⭐" if data["prize_type"] == "stars" else "Premium 💎"
    
    preview_text = (
        f"🎉 **NEW GIVEAWAY SPONSORED BY {data['host_info']}** 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 **Total Prize Pool:** {data['total_amount']} {unit_str}\n"
        f"🏆 **Winners:** {data['winner_count']} ({per_winner} {unit_str} each)\n"
        f"👑 **Hosted By:** {data['host_info']}\n"
        f"📢 **Must Join Channels:** {channels if channels else 'None'}\n"
        f"👥 **Participants:** 0 Joined\n"
        f"⏱️ **Ends At:** {data['end_time'][:16]} UTC\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        "Click the button below to verify channels and participate!"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Participate Now", callback_data=f"join_ga_{ga_id}", style="success")],
        [InlineKeyboardButton(text="🔄 Refresh Status", callback_data=f"refresh_ga_{ga_id}", style="primary")]
    ])
    
    await message.answer(f"✅ **GIVEAWAY CREATED!**\nID: `{ga_id}`\n\nForward the message below to your channels/groups:", parse_mode="Markdown")
    await message.answer(preview_text, parse_mode="Markdown", reply_markup=kb)

# --- GIVEAWAY PARTICIPATION & REFRESH HANDLERS ---

@router.callback_query(F.data.startswith("join_ga_"))
async def cb_join_giveaway(callback: CallbackQuery):
    ga_id = callback.data.replace("join_ga_", "")
    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.first_name

    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT required_channels, status FROM giveaways WHERE giveaway_id = ?", (ga_id,)) as cursor:
            ga = await cursor.fetchone()

        if not ga or ga[1] != "active":
            await callback.answer("⚠️ This giveaway has ended!", show_alert=True)
            return

        req_channels = [c.strip() for c in ga[0].split() if c.strip()]
        
        # Check channel subscriptions
        for ch in req_channels:
            try:
                member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
                if member.status in ["left", "kicked"]:
                    await callback.answer(f"❌ You must join {ch} to participate!", show_alert=True)
                    return
            except Exception:
                pass # Proceed if bot isn't admin in channel

        try:
            await db.execute("INSERT INTO giveaway_participants (giveaway_id, user_id, username) VALUES (?, ?, ?)", (ga_id, user_id, username))
            await db.commit()
            await callback.answer("🎉 You have successfully entered the giveaway!", show_alert=True)
        except aiosqlite.IntegrityError:
            await callback.answer("⚠️ You are already registered in this giveaway!", show_alert=True)

    await update_giveaway_message(callback.message, ga_id)

@router.callback_query(F.data.startswith("refresh_ga_"))
async def cb_refresh_ga(callback: CallbackQuery):
    ga_id = callback.data.replace("refresh_ga_", "")
    await update_giveaway_message(callback.message, ga_id)
    await callback.answer("Updated!")

async def update_giveaway_message(msg: Message, ga_id: str):
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT prize_type, total_amount, winner_count, host_info, required_channels, end_time, status FROM giveaways WHERE giveaway_id = ?", (ga_id,)) as cursor:
            ga = await cursor.fetchone()
        
        if not ga:
            return

        async with db.execute("SELECT COUNT(*) FROM giveaway_participants WHERE giveaway_id = ?", (ga_id,)) as cursor:
            count = (await cursor.fetchone())[0]

    prize_type, total_amount, winner_count, host_info, req_channels, end_time, status = ga
    per_winner = total_amount // winner_count
    unit_str = "Stars ⭐" if prize_type == "stars" else "Premium 💎"

    status_str = "🟢 ACTIVE" if status == "active" else "🔴 ENDED"
    
    text = (
        f"🎉 **GIVEAWAY SPONSORED BY {host_info}** 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **Status:** {status_str}\n"
        f"🎁 **Total Prize Pool:** {total_amount} {unit_str}\n"
        f"🏆 **Winners:** {winner_count} ({per_winner} {unit_str} each)\n"
        f"👑 **Hosted By:** {host_info}\n"
        f"📢 **Must Join:** {req_channels if req_channels else 'None'}\n"
        f"👥 **Participants:** `{count}` Joined\n"
        f"⏱️ **Ends At:** {end_time[:16]} UTC\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Participate Now", callback_data=f"join_ga_{ga_id}", style="success")],
        [InlineKeyboardButton(text="🔄 Refresh Status", callback_data=f"refresh_ga_{ga_id}", style="primary")]
    ])
    try:
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass

# --- ADMIN MANIPULATION & WINNER DRAWING SYSTEM ---

@router.message(Command("force_winner"))
async def cmd_force_winner(message: Message):
    if message.from_user.id != settings.ADMIN_USER_ID:
        return
        
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.answer("⚠️ Usage: `/force_winner <giveaway_id> <user_id>`", parse_mode="Markdown")
        return

    ga_id, target_user = args[0], int(args[1])
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO forced_winners (giveaway_id, user_id) VALUES (?, ?)", (ga_id, target_user))
        await db.commit()

    await message.answer(f"✅ User `{target_user}` forced as guaranteed winner for `{ga_id}`.", parse_mode="Markdown")

@router.message(Command("draw_giveaway"))
async def cmd_draw_giveaway(message: Message):
    if message.from_user.id != settings.ADMIN_USER_ID:
        return

    args = message.text.split()[1:]
    if not args:
        await message.answer("⚠️ Usage: `/draw_giveaway <giveaway_id>`", parse_mode="Markdown")
        return

    ga_id = args[0]
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT prize_type, total_amount, winner_count FROM giveaways WHERE giveaway_id = ?", (ga_id,)) as cursor:
            ga = await cursor.fetchone()

        if not ga:
            await message.answer("⚠️ Giveaway not found.")
            return

        prize_type, total_amount, winner_count = ga
        
        # Get all participants
        async with db.execute("SELECT user_id, username FROM giveaway_participants WHERE giveaway_id = ?", (ga_id,)) as cursor:
            participants = await cursor.fetchall()

        # Get forced winners
        async with db.execute("SELECT user_id FROM forced_winners WHERE giveaway_id = ?", (ga_id,)) as cursor:
            forced = [row[0] for row in await cursor.fetchall()]

        # Close giveaway
        await db.execute("UPDATE giveaways SET status = 'ended' WHERE giveaway_id = ?", (ga_id,))
        await db.commit()

    if not participants:
        await message.answer("⚠️ No participants joined this giveaway.")
        return

    all_ids = [p[0] for p in participants]
    selected_winners = []

    # Priority 1: Pick forced winners
    for f_id in forced:
        if f_id in all_ids and f_id not in selected_winners and len(selected_winners) < winner_count:
            selected_winners.append(f_id)

    # Priority 2: Fill remaining randomly
    remaining_pool = [uid for uid in all_ids if uid not in selected_winners]
    random.shuffle(remaining_pool)
    
    needed = winner_count - len(selected_winners)
    if needed > 0:
        selected_winners.extend(remaining_pool[:needed])

    # Build CSV Reports for Admin
    participants_csv = "user_id,username\n" + "\n".join([f"{p[0]},{p[1]}" for p in participants])
    winners_csv = "user_id,prize_share\n" + "\n".join([f"{w},{total_amount // winner_count}" for w in selected_winners])

    p_file = BufferedInputFile(participants_csv.encode("utf-8"), filename=f"participants_{ga_id}.csv")
    w_file = BufferedInputFile(winners_csv.encode("utf-8"), filename=f"winners_{ga_id}.csv")

    await message.answer_document(p_file, caption=f"📊 **All Participants List** ({len(participants)} Total)")
    await message.answer_document(w_file, caption=f"🏆 **Selected Winners List** ({len(selected_winners)} Winners)")

    # Notify Winners
    per_winner = total_amount // winner_count
    unit_str = "Stars ⭐" if prize_type == "stars" else "Premium Subscriptions 💎"
    
    for w_id in selected_winners:
        try:
            await bot.send_message(w_id, f"🎉 **CONGRATULATIONS!** 🎉\n\nYou won **{per_winner} {unit_str}** in Giveaway `{ga_id}`!\nThe host/admin will distribute your prize shortly.", parse_mode="Markdown")
        except Exception:
            pass

# --- BACKGROUND LIVE TASK ---

async def giveaway_scheduler():
    while True:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect(settings.DB_PATH) as db:
                async with db.execute("SELECT giveaway_id FROM giveaways WHERE status = 'active' AND end_time <= ?", (now_iso,)) as cursor:
                    expired = await cursor.fetchall()

            for ga in expired:
                ga_id = ga[0]
                logger.info(f"Auto-ending expired giveaway: {ga_id}")
                # Auto draw could be called here
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            
        await asyncio.sleep(60)

# --- WEBHOOK & APP STARTER ---

async def main():
    await init_db()
    asyncio.create_task(giveaway_scheduler())
    
    app = web.Application()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.WEBHOOK_HOST, settings.WEBHOOK_PORT)
    await site.start()
    
    logger.info("Bot Polling Started...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
