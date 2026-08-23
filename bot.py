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
    waiting_for_quantity = State()

class BoostBuyStates(StatesGroup):
    waiting_for_channel = State()

class SellStarsStates(StatesGroup):
    waiting_for_quantity = State()

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

# Dynamic Colored UI Keyboards using Telegram API style parameters
def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Buy Premium", callback_data="nav_buy_premium", style="primary"),
                InlineKeyboardButton(text="⭐ Buy Stars", callback_data="nav_buy_stars", style="success"),
            ],
            [
                InlineKeyboardButton(text="⚡ Buy Boosts", callback_data="nav_buy_boosts", style="primary"),
                InlineKeyboardButton(text="🌟 Sell Stars", callback_data="nav_sell_stars", style="danger"),
            ],
            [
                InlineKeyboardButton(text="🎁 Prepay Giveaway", callback_data="nav_giveaway", style="primary"),
                InlineKeyboardButton(text="📣 Telegram Ads", callback_data="nav_ads", style="primary"),
            ],
            [
                InlineKeyboardButton(text="👛 Wallet", callback_data="nav_wallet", style="success")
            ],
            [
                InlineKeyboardButton(text="🌐 Language", callback_data="nav_lang"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="nav_settings"),
            ],
            [
                InlineKeyboardButton(text="💬 Contact Support", callback_data="nav_support", style="primary")
            ],
            [
                InlineKeyboardButton(text="🎁 Daily Giveaways", callback_data="nav_daily_giveaway", style="success")
            ],
        ]
    )

def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel / Back", callback_data="nav_main", style="danger")],
            [InlineKeyboardButton(text="💬 Contact Support", callback_data="nav_support", style="primary")]
        ]
    )

# --- START & MENU COMMANDS ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    referrer = int(args[0].replace("REF", "")) if args and args[0].startswith("REF") and args[0][3:].isdigit() else None
    
    user_data = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        referrer=referrer
    )
    
    caption = (
        f"🌌 **WELCOME TO {settings.BOT_USERNAME.upper()}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 **Current Balance:** `${user_data['balance']:.2f} USD`\n"
        f"👤 **User ID:** `{message.from_user.id}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ Select a category below to buy Premium, Stars, Boosts, or manage your digital wallet:"
    )
    await message.answer(caption, parse_mode="Markdown", reply_markup=get_main_keyboard())

@router.callback_query(F.data == "nav_main")
async def cb_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_data = await get_or_create_user(callback.from_user.id, callback.from_user.username or "", callback.from_user.first_name or "")
    text = (
        f"🌌 **MAIN MENU**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 **Current Balance:** `${user_data['balance']:.2f} USD`\n"
        f"👤 **User ID:** `{callback.from_user.id}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        "Please choose an option below:"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# --- BUY STARS FLOW ---
@router.callback_query(F.data == "nav_buy_stars")
async def cb_buy_stars(callback: CallbackQuery):
    text = (
        "⭐ **BUY TELEGRAM STARS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select your desired package below to top up Telegram Stars instantly:\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ 100 Stars - $2.50", callback_data="buy_stars_100", style="success"),
            InlineKeyboardButton(text="⭐ 250 Stars - $5.50", callback_data="buy_stars_250", style="success")
        ],
        [
            InlineKeyboardButton(text="⭐ 500 Stars - $10.50", callback_data="buy_stars_500", style="success"),
            InlineKeyboardButton(text="⭐ 1,000 Stars - $20.00", callback_data="buy_stars_1000", style="success")
        ],
        [
            InlineKeyboardButton(text="⭐ 2,500 Stars - $48.00", callback_data="buy_stars_2500", style="primary"),
            InlineKeyboardButton(text="⭐ 5,000 Stars - $95.00", callback_data="buy_stars_5000", style="primary")
        ],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")],
        [InlineKeyboardButton(text="💬 Contact Support", callback_data="nav_support", style="primary")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("buy_stars_"))
async def cb_select_stars_pkg(callback: CallbackQuery, state: FSMContext):
    amount_str = callback.data.split("_")[2]
    await state.update_data(stars_qty=amount_str)
    await state.set_state(StarsBuyStates.waiting_for_recipient)
    
    text = (
        f"⭐ **CONFIRM STARS RECIPIENT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Selected Package: **{amount_str} Telegram Stars**\n\n"
        f"Please send the target `@username` or Telegram User ID to receive the delivery:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Buy for Myself", callback_data="stars_self", style="success")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "stars_self")
async def cb_stars_self(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    qty = data.get("stars_qty", "100")
    recipient = f"@{callback.from_user.username}" if callback.from_user.username else str(callback.from_user.id)
    await finalize_stars_order(callback.message, callback.from_user.id, qty, recipient, state)

@router.message(StarsBuyStates.waiting_for_recipient)
async def process_stars_recipient(message: Message, state: FSMContext):
    data = await state.get_data()
    qty = data.get("stars_qty", "100")
    recipient = message.text.strip()
    await finalize_stars_order(message, message.from_user.id, qty, recipient, state)

async def finalize_stars_order(message_obj, user_id: int, qty: str, recipient: str, state: FSMContext):
    await state.clear()
    order_id = generate_order_id()
    
    text = (
        f"🧾 **ORDER SUMMARY & PAYMENT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Order ID:** `{order_id}`\n"
        f"**Item:** Telegram Stars ({qty}x)\n"
        f"**Recipient:** `{recipient}`\n"
        f"**Status:** Pending Payment\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        "Click below to proceed with crypto checkout via OxaPay:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay with OxaPay Crypto", url=f"https://oxapay.com/pay/{order_id}", style="success")],
        [InlineKeyboardButton(text="❌ Cancel Order", callback_data="nav_main", style="danger")]
    ])
    if isinstance(message_obj, Message):
        await message_obj.answer(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await message_obj.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# --- BUY PREMIUM FLOW ---
@router.callback_query(F.data == "nav_buy_premium")
async def cb_buy_premium(callback: CallbackQuery):
    text = (
        "💎 **BUY TELEGRAM PREMIUM**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select your preferred Telegram Premium plan:\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 3 Months Premium - $13.99", callback_data="prem_3m", style="success")],
        [InlineKeyboardButton(text="⭐ 6 Months Premium - $22.99", callback_data="prem_6m", style="success")],
        [InlineKeyboardButton(text="⭐ 12 Months Premium - $39.99", callback_data="prem_12m", style="primary")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# --- BUY BOOSTS FLOW ---
@router.callback_query(F.data == "nav_buy_boosts")
async def cb_buy_boosts(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BoostBuyStates.waiting_for_channel)
    text = (
        "⚡ **BUY CHANNEL BOOSTS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Which channel or group should be boosted?\n\n"
        "Please send the `@username`, `https://t.me/...` link, or invite link below:"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# --- SELL STARS FLOW ---
@router.callback_query(F.data == "nav_sell_stars")
async def cb_sell_stars(callback: CallbackQuery):
    text = (
        "🌟 **SELL TELEGRAM STARS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "**Limit:** 100 - 100,000 ⭐\n\n"
        "⚙️ **Important to know:**\n"
        "• Transferring Stars via bot incurs standard 15% system fee.\n"
        "• Payouts are processed smoothly to your wallet balance.\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌟 View Sale History", callback_data="stars_history", style="primary")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")],
        [InlineKeyboardButton(text="💬 Contact Support", callback_data="nav_support", style="primary")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# --- GIVEAWAY & ADS FLOW ---
@router.callback_query(F.data == "nav_giveaway")
async def cb_giveaway(callback: CallbackQuery):
    text = (
        "🎁 **PREPAY A GIVEAWAY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Sponsor giveaways directly for your channel or community.\n"
        "Select prize format below:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Stars Giveaway", callback_data="ga_stars", style="success")],
        [InlineKeyboardButton(text="💎 Premium Giveaway", callback_data="ga_prem", style="primary")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "nav_daily_giveaway")
async def cb_daily_giveaway(callback: CallbackQuery):
    text = (
        "🎁 **DAILY FREEBIES GIVEAWAY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "**Prize:** 100 Telegram Stars\n"
        "**Participants:** 617\n"
        "**Ends In:** 04 Hours\n\n"
        "Press the button below to join today's giveaway!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Enter Giveaway", callback_data="join_daily_ga", style="success")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# --- WALLET & DEPOSIT FLOW ---
@router.callback_query(F.data == "nav_wallet")
async def cb_wallet(callback: CallbackQuery):
    user_data = await get_or_create_user(callback.from_user.id, callback.from_user.username or "", callback.from_user.first_name or "")
    text = (
        f"👛 **WALLET OVERVIEW**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 **Available Balance:** `${user_data['balance']:.2f} USD`\n"
        f"📥 **Total Deposited:** `${user_data['total_deposited']:.2f} USD`\n"
        f"🛍️ **Total Spent:** `${user_data['total_spent']:.2f} USD`\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Deposit Crypto", callback_data="wallet_deposit", style="success")],
        [InlineKeyboardButton(text="📜 Transaction History", callback_data="wallet_history", style="primary")],
        [InlineKeyboardButton(text="💸 Withdraw", callback_data="wallet_withdraw", style="danger")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "wallet_deposit")
async def cb_deposit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DepositStates.waiting_for_amount)
    text = (
        "💵 **DEPOSIT FUNDS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Please enter the deposit amount in **USD** (Minimum $5.00):"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

@router.message(DepositStates.waiting_for_amount)
async def process_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount < 5.0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid numerical amount (minimum $5.00).")
        return

    await state.clear()
    order_id = generate_order_id()
    
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO deposits (track_id, order_id, user_id, amount, status) VALUES (?, ?, ?, ?, ?)",
            (f"TRK-{order_id}", order_id, message.from_user.id, amount, "pending")
        )
        await db.commit()

    text = (
        f"💳 **SELECT DEPOSIT CURRENCY**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Order ID: `{order_id}`\n"
        f"Amount: **${amount:.2f} USD**\n\n"
        "Select your preferred cryptocurrency gateway:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="₿ BTC", url=f"https://oxapay.com/pay/{order_id}", style="primary"),
            InlineKeyboardButton(text="Ł LTC", url=f"https://oxapay.com/pay/{order_id}", style="primary"),
            InlineKeyboardButton(text="⟠ ETH", url=f"https://oxapay.com/pay/{order_id}", style="primary")
        ],
        [
            InlineKeyboardButton(text="💎 TON", url=f"https://oxapay.com/pay/{order_id}", style="primary"),
            InlineKeyboardButton(text="₮ USDT (TRC20)", url=f"https://oxapay.com/pay/{order_id}", style="success"),
            InlineKeyboardButton(text="☀️ SOL", url=f"https://oxapay.com/pay/{order_id}", style="primary")
        ],
        [InlineKeyboardButton(text="❌ Cancel Deposit", callback_data="nav_main", style="danger")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

# --- SUPPORT FLOW ---
@router.callback_query(F.data == "nav_support")
async def cb_support(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportStates.waiting_for_message)
    text = (
        "💬 **CUSTOMER SUPPORT**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Describe your issue or order inquiry in detail below. Support personnel will reply directly to your chat."
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# --- WEBHOOK & HTTP SERVERS ---
async def oxapay_webhook_handler(request: web.Request):
    signature = request.headers.get("HMAC", "")
    body = await request.read()
    
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
                        await bot.send_message(user_id, f"✅ **Deposit Confirmed!**\n\nYour balance of **${amount:.2f} USD** has been credited.", parse_mode="Markdown")
                    except Exception as e:
                        logger.error(f"Failed to notify user {user_id}: {e}")

    return web.Response(status=200, text="OK")

async def health_check_handler(request: web.Request):
    return web.json_response({"status": "ok", "bot": settings.BOT_USERNAME})

# --- APPLICATION RUNNER ---
async def main():
    await init_db()
    
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
