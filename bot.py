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

class GiveawayStates(StatesGroup):
    waiting_for_prize = State()
    waiting_for_winners = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()

# Database Setup
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
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                user_id INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
    logger.info("SQLite database tables initialized successfully.")

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

# --- KEYBOARD BUILDERS WITH THEMED BUTTON STYLES ---

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
        "✨ Premium Telegram Store Automation Platform.\n"
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
    
    # Create pending order in DB
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

        # Deduct balance atomically
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

# --- GIVEAWAYS & ADS ---

@router.callback_query(F.data == "nav_giveaway")
async def cb_giveaway(callback: CallbackQuery):
    text = (
        "🎁 **PREPAY A GIVEAWAY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Host sponsored giveaways for your channels:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Stars Giveaway", callback_data="ga_stars", style="success")],
        [InlineKeyboardButton(text="💎 Premium Giveaway", callback_data="ga_prem", style="primary")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "nav_daily_giveaway")
async def cb_daily_giveaway(callback: CallbackQuery):
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM giveaway_entries") as cursor:
            count = (await cursor.fetchone())[0]

    text = (
        "🎁 **DAILY FREEBIES GIVEAWAY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Prize: **100 Telegram Stars**\n"
        f"Total Participants: **{count:,}**\n"
        "Ends In: **04 Hours**\n\n"
        "Press the button below to participate!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Enter Giveaway", callback_data="join_daily_ga", style="success")],
        [InlineKeyboardButton(text="❌ Back To Menu", callback_data="nav_main", style="danger")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "join_daily_ga")
async def cb_join_daily(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(settings.DB_PATH) as db:
        try:
            await db.execute("INSERT INTO giveaway_entries (user_id) VALUES (?)", (user_id,))
            await db.commit()
            await callback.answer("🎉 Entry submitted! Good luck!", show_alert=True)
        except aiosqlite.IntegrityError:
            await callback.answer("⚠️ You have already entered today's giveaway!", show_alert=True)

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
    logger.info(f"HTTP Webhook Server active on http://{settings.WEBHOOK_HOST}:{settings.WEBHOOK_PORT}")

    logger.info("Starting Telegram Bot Polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
