import asyncio
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

# Global Bot & Dispatcher Instances
bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Pricing Constants
STAR_PRICE_PER_UNIT = 0.022  # $0.022 USD per Star
STAR_SELL_RATE = 0.015       # $0.015 USD payout per Star

# FSM States
class DepositStates(StatesGroup):
    waiting_for_amount = State()

class CustomStarsStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_recipient = State()

class PremiumStates(StatesGroup):
    waiting_for_recipient = State()

class BoostBuyStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_package = State()

class SellStarsStates(StatesGroup):
    waiting_for_quantity = State()

class AdvancedGiveawayStates(StatesGroup):
    waiting_for_prize_type = State()
    waiting_for_total_amount = State()
    waiting_for_winner_count = State()
    waiting_for_duration = State()
    waiting_for_host = State()
    waiting_for_channels = State()

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
    logger.info("Database tables initialized successfully.")

# Helper Functions
def generate_order_id() -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand_str = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"ORD-{date_str}-{rand_str}"

def generate_id(prefix="GA") -> str:
    rand_str = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{rand_str}"

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

# --- KEYBOARD BUILDERS ---

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
                InlineKeyboardButton(text="🎁 Create Advanced Giveaway", callback_data="nav_create_giveaway", style="success")
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

# --- START & MAIN MENU HANDLERS ---

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
        "✨ Premium Telegram Store & Giveaway Automation Platform.\n"
        "Select an option below to get started:"
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
        "Choose a store category from below:"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# --- BUY STARS CUSTOM & PACKAGE FLOW ---

@router.callback_query(F.data == "nav_buy_stars")
async def cb_buy_stars(callback: CallbackQuery):
    text = (
        "⭐ **BUY TELEGRAM STARS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select a popular package or choose **Custom Amount** to specify any quantity:\n"
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
        [
            InlineKeyboardButton(text="✏️ Type Custom Amount ⭐", callback_data="buy_stars_custom", style="primary")
        ],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")],
        [InlineKeyboardButton(text="💬 Contact Support", callback_data="nav_support", style="primary")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "buy_stars_custom")
async def cb_stars_custom(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CustomStarsStates.waiting_for_quantity)
    text = (
        "✍️ **CUSTOM STARS QUANTITY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "How many Stars would you like to purchase?\n\n"
        "📌 *Enter any number between 10 and 100,000:* "
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

@router.message(CustomStarsStates.waiting_for_quantity)
async def process_custom_stars_qty(message: Message, state: FSMContext):
    try:
        qty = int(message.text.strip())
        if qty < 10 or qty > 100000:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid whole number between 10 and 100,000.")
        return

    price = qty * STAR_PRICE_PER_UNIT
    await state.update_data(stars_qty=qty, total_price=price)
    await state.set_state(CustomStarsStates.waiting_for_recipient)

    text = (
        f"⭐ **TARGET RECIPIENT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Quantity: **{qty:,} Stars**\n"
        f"Total Cost: **${price:.2f} USD**\n\n"
        f"Who is this gift/top-up for?\n"
        f"• Click **Buy For Myself**\n"
        f"• Or **TYPE and SEND** the recipient's `@username` below:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Buy For Myself", callback_data="stars_target_self", style="success")],
        [InlineKeyboardButton(text="❌ Cancel Order", callback_data="nav_main", style="danger")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("buy_stars_") & (F.data != "buy_stars_custom"))
async def cb_select_preset_stars(callback: CallbackQuery, state: FSMContext):
    qty = int(callback.data.split("_")[2])
    price = qty * STAR_PRICE_PER_UNIT
    await state.update_data(stars_qty=qty, total_price=price)
    await state.set_state(CustomStarsStates.waiting_for_recipient)

    text = (
        f"⭐ **TARGET RECIPIENT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Quantity: **{qty:,} Stars**\n"
        f"Total Cost: **${price:.2f} USD**\n\n"
        f"Who is this gift/top-up for?\n"
        f"• Click **Buy For Myself**\n"
        f"• Or **TYPE and SEND** the recipient's `@username` below:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Buy For Myself", callback_data="stars_target_self", style="success")],
        [InlineKeyboardButton(text="❌ Cancel Order", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "stars_target_self")
async def cb_stars_self_target(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    qty = data.get("stars_qty", 100)
    price = data.get("total_price", 2.50)
    recipient = f"@{callback.from_user.username}" if callback.from_user.username else str(callback.from_user.id)
    await show_stars_payment_options(callback.message, callback.from_user.id, qty, price, recipient, state)

@router.message(CustomStarsStates.waiting_for_recipient)
async def process_stars_recipient_input(message: Message, state: FSMContext):
    data = await state.get_data()
    qty = data.get("stars_qty", 100)
    price = data.get("total_price", 2.50)
    recipient = message.text.strip()
    if not recipient.startswith("@") and not recipient.isdigit():
        recipient = f"@{recipient}"
    await show_stars_payment_options(message, message.from_user.id, qty, price, recipient, state)

async def show_stars_payment_options(msg_obj, user_id: int, qty: int, price: float, recipient: str, state: FSMContext):
    await state.clear()
    order_id = generate_order_id()
    
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders (order_id, user_id, product_type, product_id, amount, status, recipient) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_id, user_id, "stars", f"{qty}_stars", price, "awaiting_payment", recipient)
        )
        await db.commit()

    text = (
        f"📋 **ORDER DETAILS GENERATED**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **Order ID:** `{order_id}`\n"
        f"⭐ **Product:** Telegram Stars ({qty:,}x)\n"
        f"👤 **Target Recipient:** `{recipient}`\n"
        f"💵 **Total Payable:** `${price:.2f} USD`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        "Please choose your payment method below to proceed:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👛 Pay using Wallet Balance", callback_data=f"pay_bal_{order_id}", style="success")],
        [InlineKeyboardButton(text="💳 Pay with OxaPay Crypto", url=f"https://oxapay.com/pay/{order_id}", style="primary")],
        [InlineKeyboardButton(text="❌ Cancel Order", callback_data="nav_main", style="danger")]
    ])
    if isinstance(msg_obj, Message):
        await msg_obj.answer(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await msg_obj.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# --- BUY PREMIUM FLOW ---

@router.callback_query(F.data == "nav_buy_premium")
async def cb_buy_premium(callback: CallbackQuery):
    text = (
        "💎 **BUY TELEGRAM PREMIUM**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Choose a Telegram Premium subscription duration:\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 3 Months Premium - $13.99", callback_data="prem_select_3m", style="success")],
        [InlineKeyboardButton(text="⭐ 6 Months Premium - $22.99", callback_data="prem_select_6m", style="success")],
        [InlineKeyboardButton(text="⭐ 12 Months Premium - $39.99", callback_data="prem_select_12m", style="primary")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("prem_select_"))
async def cb_premium_recipient(callback: CallbackQuery, state: FSMContext):
    plan = callback.data.split("_")[2]
    prices = {"3m": 13.99, "6m": 22.99, "12m": 39.99}
    price = prices.get(plan, 13.99)
    await state.update_data(prem_plan=plan, total_price=price)
    await state.set_state(PremiumStates.waiting_for_recipient)

    text = (
        f"💎 **TELEGRAM PREMIUM RECIPIENT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Plan Duration: **{plan.upper()} Subscription**\n"
        f"Cost: **${price:.2f} USD**\n\n"
        f"Send the `@username` of the target user receiving Premium, or click below:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Buy For Myself", callback_data="prem_target_self", style="success")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "prem_target_self")
async def cb_prem_self(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan = data.get("prem_plan", "3m")
    price = data.get("total_price", 13.99)
    recipient = f"@{callback.from_user.username}" if callback.from_user.username else str(callback.from_user.id)
    await show_premium_checkout(callback.message, callback.from_user.id, plan, price, recipient, state)

@router.message(PremiumStates.waiting_for_recipient)
async def process_prem_recipient_input(message: Message, state: FSMContext):
    data = await state.get_data()
    plan = data.get("prem_plan", "3m")
    price = data.get("total_price", 13.99)
    recipient = message.text.strip()
    if not recipient.startswith("@") and not recipient.isdigit():
        recipient = f"@{recipient}"
    await show_premium_checkout(message, message.from_user.id, plan, price, recipient, state)

async def show_premium_checkout(msg_obj, user_id: int, plan: str, price: float, recipient: str, state: FSMContext):
    await state.clear()
    order_id = generate_order_id()
    
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders (order_id, user_id, product_type, product_id, amount, status, recipient) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_id, user_id, "premium", f"premium_{plan}", price, "awaiting_payment", recipient)
        )
        await db.commit()

    text = (
        f"📋 **PREMIUM ORDER CREATED**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **Order ID:** `{order_id}`\n"
        f"💎 **Package:** Premium ({plan.upper()})\n"
        f"👤 **Target Recipient:** `{recipient}`\n"
        f"💵 **Price:** `${price:.2f} USD`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select payment method:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👛 Pay using Wallet Balance", callback_data=f"pay_bal_{order_id}", style="success")],
        [InlineKeyboardButton(text="💳 Pay with OxaPay Crypto", url=f"https://oxapay.com/pay/{order_id}", style="primary")],
        [InlineKeyboardButton(text="❌ Cancel Order", callback_data="nav_main", style="danger")]
    ])
    if isinstance(msg_obj, Message):
        await msg_obj.answer(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await msg_obj.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# --- WALLET BALANCE PAYMENT HANDLER ---

@router.callback_query(F.data.startswith("pay_bal_"))
async def cb_pay_via_balance(callback: CallbackQuery):
    order_id = callback.data.replace("pay_bal_", "")
    user_id = callback.from_user.id

    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT amount, status, product_type, recipient FROM orders WHERE order_id = ?", (order_id,)) as cursor:
            order = await cursor.fetchone()
        
        if not order:
            await callback.answer("⚠️ Order not found.", show_alert=True)
            return
            
        amount, status, ptype, recipient = order
        if status != "awaiting_payment":
            await callback.answer("⚠️ Order is no longer active.", show_alert=True)
            return

        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            
        balance = user[0] if user else 0.0

        if balance < amount:
            await callback.answer(f"❌ Insufficient balance! Required: ${amount:.2f}, Available: ${balance:.2f}", show_alert=True)
            return

        await db.execute("UPDATE users SET balance = balance - ?, total_spent = total_spent + ? WHERE user_id = ?", (amount, amount, user_id))
        await db.execute("UPDATE orders SET status = 'completed', delivery_status = 'processing' WHERE order_id = ?", (order_id,))
        await db.commit()

    text = (
        f"✅ **PAYMENT SUCCESSFUL**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Order ID: `{order_id}`\n"
        f"💵 Amount Deducted: `${amount:.2f} USD`\n"
        f"🎯 Recipient: `{recipient}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎉 Your order is sent for fulfillment!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="nav_main", style="primary")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# --- BUY BOOSTS FLOW ---

@router.callback_query(F.data == "nav_buy_boosts")
async def cb_buy_boosts(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BoostBuyStates.waiting_for_channel)
    text = (
        "⚡ **BUY CHANNEL BOOSTS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send the target `@username` or channel invite link below:"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

@router.message(BoostBuyStates.waiting_for_channel)
async def process_boost_channel(message: Message, state: FSMContext):
    channel = message.text.strip()
    await state.update_data(target_channel=channel)
    
    text = (
        f"⚡ **SELECT BOOST PACKAGE**\n"
        f"Target Channel: `{channel}`\n\n"
        "Select number of boosts:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ 4 Boosts - $10.00", callback_data="boost_4", style="success")],
        [InlineKeyboardButton(text="⚡ 10 Boosts - $22.00", callback_data="boost_10", style="success")],
        [InlineKeyboardButton(text="⚡ 20 Boosts - $40.00", callback_data="boost_20", style="primary")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="nav_main", style="danger")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

# --- SELL STARS FLOW ---

@router.callback_query(F.data == "nav_sell_stars")
async def cb_sell_stars(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SellStarsStates.waiting_for_quantity)
    text = (
        "🌟 **SELL TELEGRAM STARS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Payout Rate: **$0.015 USD per Star**\n"
        "Minimum: **100 ⭐** | Maximum: **100,000 ⭐**\n\n"
        "✍️ Type how many Stars you would like to sell:"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

@router.message(SellStarsStates.waiting_for_quantity)
async def process_sell_stars(message: Message, state: FSMContext):
    try:
        qty = int(message.text.strip())
        if qty < 100 or qty > 100000:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid quantity between 100 and 100,000 Stars.")
        return

    payout = qty * STAR_SELL_RATE
    await state.clear()
    
    text = (
        f"🌟 **SELL ESTIMATE**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Selling: **{qty:,} Stars**\n"
        f"You Receive: **${payout:.2f} USD**\n\n"
        "Click below to initiate manual transfer:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Submit Sale Request", callback_data="submit_star_sale", style="success")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="nav_main", style="danger")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

# --- PREPAY GIVEAWAYS & ADS ---

@router.callback_query(F.data == "nav_giveaway")
async def cb_giveaway(callback: CallbackQuery):
    text = (
        "🎁 **PREPAY A GIVEAWAY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Host sponsored giveaways for your channels:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Create Advanced Giveaway", callback_data="nav_create_giveaway", style="success")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "nav_ads")
async def cb_ads(callback: CallbackQuery):
    text = (
        "📣 **TELEGRAM ADS & PROMOTIONS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Promote your channels to active store users.\n\n"
        "• Broadcast Messages\n"
        "• Main Menu Banners\n"
        "Contact support for pricing."
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

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
    
    unit = "Stars" if prize_type == "stars" else "Premium Subscriptions"
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
        
        for ch in req_channels:
            try:
                member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
                if member.status in ["left", "kicked"]:
                    await callback.answer(f"❌ You must join {ch} to participate!", show_alert=True)
                    return
            except Exception:
                pass

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
        
        async with db.execute("SELECT user_id, username FROM giveaway_participants WHERE giveaway_id = ?", (ga_id,)) as cursor:
            participants = await cursor.fetchall()

        async with db.execute("SELECT user_id FROM forced_winners WHERE giveaway_id = ?", (ga_id,)) as cursor:
            forced = [row[0] for row in await cursor.fetchall()]

        await db.execute("UPDATE giveaways SET status = 'ended' WHERE giveaway_id = ?", (ga_id,))
        await db.commit()

    if not participants:
        await message.answer("⚠️ No participants joined this giveaway.")
        return

    all_ids = [p[0] for p in participants]
    selected_winners = []

    for f_id in forced:
        if f_id in all_ids and f_id not in selected_winners and len(selected_winners) < winner_count:
            selected_winners.append(f_id)

    remaining_pool = [uid for uid in all_ids if uid not in selected_winners]
    random.shuffle(remaining_pool)
    
    needed = winner_count - len(selected_winners)
    if needed > 0:
        selected_winners.extend(remaining_pool[:needed])

    participants_csv = "user_id,username\n" + "\n".join([f"{p[0]},{p[1]}" for p in participants])
    winners_csv = "user_id,prize_share\n" + "\n".join([f"{w},{total_amount // winner_count}" for w in selected_winners])

    p_file = BufferedInputFile(participants_csv.encode("utf-8"), filename=f"participants_{ga_id}.csv")
    w_file = BufferedInputFile(winners_csv.encode("utf-8"), filename=f"winners_{ga_id}.csv")

    await message.answer_document(p_file, caption=f"📊 **All Participants List** ({len(participants)} Total)")
    await message.answer_document(w_file, caption=f"🏆 **Selected Winners List** ({len(selected_winners)} Winners)")

    per_winner = total_amount // winner_count
    unit_str = "Stars ⭐" if prize_type == "stars" else "Premium Subscriptions 💎"
    
    for w_id in selected_winners:
        try:
            await bot.send_message(w_id, f"🎉 **CONGRATULATIONS!** 🎉\n\nYou won **{per_winner} {unit_str}** in Giveaway `{ga_id}`!\nThe host/admin will distribute your prize shortly.", parse_mode="Markdown")
        except Exception:
            pass

# --- WALLET & DEPOSIT SYSTEM ---

@router.callback_query(F.data == "nav_wallet")
async def cb_wallet(callback: CallbackQuery):
    user_data = await get_or_create_user(callback.from_user.id, callback.from_user.username or "", callback.from_user.first_name or "")
    text = (
        f"👛 **WALLET OVERVIEW**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 **Balance:** `${user_data['balance']:.2f} USD`\n"
        f"📥 **Deposited:** `${user_data['total_deposited']:.2f} USD`\n"
        f"🛍️ **Spent:** `${user_data['total_spent']:.2f} USD`\n"
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
        "Enter deposit amount in USD (Minimum $5.00):"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

@router.message(DepositStates.waiting_for_amount)
async def process_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount < 5.0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Minimum deposit amount is $5.00.")
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
        f"💳 **CRYPTO DEPOSIT**\n"
        f"Order ID: `{order_id}`\n"
        f"Amount: **${amount:.2f} USD**\n\n"
        "Click below to make payment via OxaPay:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay with OxaPay", url=f"https://oxapay.com/pay/{order_id}", style="success")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="nav_main", style="danger")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

# --- SETTINGS, LANGUAGE & SUPPORT ---

@router.callback_query(F.data == "nav_settings")
async def cb_settings(callback: CallbackQuery):
    text = "⚙️ **BOT SETTINGS**\n\nManage notifications and preferences:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Notifications: ON", callback_data="toggle_notif", style="success")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "nav_lang")
async def cb_lang(callback: CallbackQuery):
    text = "🌐 **SELECT LANGUAGE**\n\nChoose language:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en", style="success")],
        [InlineKeyboardButton(text="🇮🇳 Hindi", callback_data="lang_hi", style="primary")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "nav_support")
async def cb_support(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportStates.waiting_for_message)
    text = (
        "💬 **CUSTOMER SUPPORT**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Type your message/inquiry below. Support will respond shortly:"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# --- WEBHOOK & BACKGROUND TASKS ---

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
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            
        await asyncio.sleep(60)

# --- APPLICATION RUNNER ---

async def main():
    await init_db()
    asyncio.create_task(giveaway_scheduler())
    
    app = web.Application()
    app.router.add_post("/oxapay/webhook", oxapay_webhook_handler)
    app.router.add_get("/health", health_check_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.WEBHOOK_HOST, settings.WEBHOOK_PORT)
    await site.start()
    logger.info(f"HTTP Webhook Server active on http://{settings.WEBHOOK_HOST}:{settings.WEBHOOK_PORT}")

    logger.info("Starting Telegram Bot Polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
