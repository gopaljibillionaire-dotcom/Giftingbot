import asyncio
import hmac
import hashlib
import logging
import random
import string
import sys
from datetime import datetime, timezone
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
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

# FSM States
class DepositStates(StatesGroup):
    waiting_for_amount = State()

class StarsBuyStates(StatesGroup):
    waiting_for_recipient = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()

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
                referred_by INTEGER,
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
                delivery_status TEXT,
                recipient TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                track_id TEXT PRIMARY KEY,
                order_id TEXT,
                user_id INTEGER,
                amount REAL,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
    logger.info("Database schemas initialized cleanly.")

# Helper Functions
def generate_order_id() -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand_str = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"ORD-{date_str}-{rand_str}"

async def get_or_create_user(user_id: int, username: str, first_name: str, referrer: int = None):
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT user_id, balance, total_spent, total_deposited FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute(
                    "INSERT INTO users (user_id, username, first_name, referred_by) VALUES (?, ?, ?, ?)",
                    (user_id, username, first_name, referrer),
                )
                await db.commit()
                return {"user_id": user_id, "balance": 0.0, "total_spent": 0.0, "total_deposited": 0.0}
            return {"user_id": user[0], "balance": user[1], "total_spent": user[2], "total_deposited": user[3]}

# Keyboards
def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Buy Premium", callback_data="nav_buy_premium"),
                InlineKeyboardButton(text="⭐ Buy Stars", callback_data="nav_buy_stars"),
            ],
            [
                InlineKeyboardButton(text="⚡ Buy Boosts", callback_data="nav_buy_boosts"),
                InlineKeyboardButton(text="🌟 Sell Stars", callback_data="nav_sell_stars"),
            ],
            [
                InlineKeyboardButton(text="🎁 Prepay Giveaway", callback_data="nav_giveaway"),
                InlineKeyboardButton(text="📣 Telegram Ads", callback_data="nav_ads"),
            ],
            [InlineKeyboardButton(text="👛 Wallet", callback_data="nav_wallet")],
            [
                InlineKeyboardButton(text="🌐 Language", callback_data="nav_lang"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="nav_settings"),
            ],
            [InlineKeyboardButton(text="💬 Contact Support", callback_data="nav_support")],
            [InlineKeyboardButton(text="🎁 Daily Giveaways", callback_data="nav_daily_giveaway")],
        ]
    )

def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Back", callback_data="nav_main")],
            [InlineKeyboardButton(text="💬 Contact Support", callback_data="nav_support")]
        ]
    )

# Handlers
@router.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    referrer = int(args[0].replace("REF", "")) if args and args[0].startswith("REF") and args[0][3:].isdigit() else None
    
    user_data = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        referrer=referrer
    )
    
    caption = (
        f"🌟 **Welcome to {settings.BOT_USERNAME}**\n\n"
        f"💰 **Current Balance:** ${user_data['balance']:.2f}\n\n"
        "Select an option from the menu below to browse Premium gifts, Stars, Boosts, or manage your wallet."
    )
    await message.answer(caption, parse_mode="Markdown", reply_markup=get_main_keyboard())

@router.callback_query(F.data == "nav_main")
async def cb_main(callback: CallbackQuery):
    user_data = await get_or_create_user(callback.from_user.id, callback.from_user.username or "", callback.from_user.first_name or "")
    text = (
        f"🌟 **Welcome to {settings.BOT_USERNAME}**\n\n"
        f"💰 **Current Balance:** ${user_data['balance']:.2f}\n\n"
        "Please choose an option below."
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

@router.callback_query(F.data == "nav_wallet")
async def cb_wallet(callback: CallbackQuery):
    user_data = await get_or_create_user(callback.from_user.id, callback.from_user.username or "", callback.from_user.first_name or "")
    text = (
        f"👛 **Wallet Overview**\n\n"
        f"💰 **Available Balance:** ${user_data['balance']:.2f}\n"
        f"📥 **Total Deposited:** ${user_data['total_deposited']:.2f}\n"
        f"🛍️ **Total Spent:** ${user_data['total_spent']:.2f}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Deposit Crypto", callback_data="wallet_deposit")],
        [InlineKeyboardButton(text="📜 Transaction History", callback_data="wallet_history")],
        [InlineKeyboardButton(text="💸 Withdraw", callback_data="wallet_withdraw")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="nav_main")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "wallet_deposit")
async def cb_deposit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DepositStates.waiting_for_amount)
    await callback.message.edit_text(
        "💵 **Deposit Funds**\n\nPlease enter the amount in **USD** you wish to deposit (Min: $5.00):",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )

@router.message(DepositStates.waiting_for_amount)
async def process_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount < 5.0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid numerical amount greater than or equal to $5.00.")
        return

    await state.clear()
    order_id = generate_order_id()
    
    # Store pending deposit state in DB
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO deposits (track_id, order_id, user_id, amount, status) VALUES (?, ?, ?, ?, ?)",
            (f"TRK-{order_id}", order_id, message.from_user.id, amount, "pending")
        )
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay with Crypto (OxaPay)", url=f"https://oxapay.com/pay/{order_id}")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="nav_main")]
    ])
    
    await message.answer(
        f"🧾 **Invoice Created!**\n\n"
        f"**Order ID:** `{order_id}`\n"
        f"**Amount:** ${amount:.2f} USD\n\n"
        "Click below to complete the deposit. Your balance will be credited automatically via webhook upon block confirmation.",
        parse_mode="Markdown",
        reply_markup=kb
    )

@router.callback_query(F.data == "nav_buy_stars")
async def cb_buy_stars(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 100 Stars - $2.50", callback_data="stars_100")],
        [InlineKeyboardButton(text="⭐ 500 Stars - $11.00", callback_data="stars_500")],
        [InlineKeyboardButton(text="⭐ 1,000 Stars - $20.00", callback_data="stars_1000")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="nav_main")]
    ])
    await callback.message.edit_text("⭐ **Select Telegram Stars Package**\n\nChoose the desired amount:", parse_mode="Markdown", reply_markup=kb)

# Webhook Endpoint (OxaPay)
async def oxapay_webhook_handler(request: web.Request):
    signature = request.headers.get("HMAC", "")
    body = await request.read()
    
    # Signature Verification
    if settings.OXAPAY_MERCHANT_API_KEY:
        computed_sig = hmac.new(
            settings.OXAPAY_MERCHANT_API_KEY.encode('utf-8'),
            body,
            hashlib.sha512
        ).hexdigest()
        if not hmac.compare_digest(computed_sig, signature):
            logger.warning("Invalid OxaPay HMAC signature received.")
            return web.Response(status=400, text="Invalid Signature")

    payload = await request.json()
    track_id = payload.get("trackId")
    status = payload.get("status")
    
    if status == "Paid":
        async with aiosqlite.connect(settings.DB_PATH) as db:
            async with db.execute("SELECT user_id, amount, status FROM deposits WHERE track_id = ?", (track_id,)) as cursor:
                deposit = await cursor.fetchone()
                if deposit and deposit[2] != "completed":
                    user_id, amount, _ = deposit
                    await db.execute("UPDATE deposits SET status = 'completed' WHERE track_id = ?", (track_id,))
                    await db.execute("UPDATE users SET balance = balance + ?, total_deposited = total_deposited + ? WHERE user_id = ?", (amount, amount, user_id))
                    await db.commit()
                    
                    try:
                        await bot.send_message(user_id, f"✅ **Deposit Confirmed!**\n\nYour deposit of **${amount:.2f}** has been credited to your balance.", parse_mode="Markdown")
                    except Exception as e:
                        logger.error(f"Failed to notify user {user_id}: {e}")

    return web.Response(status=200, text="OK")

async def health_check_handler(request: web.Request):
    return web.json_response({"status": "ok", "service": "Telegram Gifting Bot"})

# Startup Execution
async def main():
    await init_db()
    
    # Configure WebServer for HTTP/Webhooks
    app = web.Application()
    app.router.add_post("/oxapay/webhook", oxapay_webhook_handler)
    app.router.add_get("/health", health_check_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.WEBHOOK_HOST, settings.WEBHOOK_PORT)
    await site.start()
    logger.info(f"HTTP Server active on http://{settings.WEBHOOK_HOST}:{settings.WEBHOOK_PORT}")

    logger.info("Starting Telegram Bot Polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
