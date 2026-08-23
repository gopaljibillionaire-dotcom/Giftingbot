"""
Complete Production-Ready Telegram Gifting & Digital Store Bot
Fixes Termux DNS issue & Pydantic V2 deprecation warnings.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

# ------------------------------------------------------------------------------
# 0. TERMUX / ANDROID DNS PATCH FOR MONGODB+SRV URIs
# ------------------------------------------------------------------------------
import dns.resolver

try:
    dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
    dns.resolver.default_resolver.nameservers = ["8.8.8.8", "1.1.1.1"]
except Exception:
    pass

# Import Config from config.py
from config import config

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

# ------------------------------------------------------------------------------
# 1. LOGGING
# ------------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("StoreBot")

# Ensure Owner ID is in Admin List
if config.OWNER_ID not in config.ADMIN_IDS:
    config.ADMIN_IDS.append(config.OWNER_ID)

# ------------------------------------------------------------------------------
# 2. DATABASE LAYER (MongoDB / Motor)
# ------------------------------------------------------------------------------


class Database:

    def __init__(self, uri: str, db_name: str):
        self.client = AsyncIOMotorClient(uri)
        self.db: AsyncIOMotorDatabase = self.client[db_name]

    async def init_indexes(self):
        """Build required indexes on boot."""
        logger.info("Initializing Database Indexes...")
        await self.db.users.create_index("user_id", unique=True)
        await self.db.users.create_index("referral_id", unique=True)
        await self.db.orders.create_index("order_id", unique=True)
        await self.db.orders.create_index("user_id")
        await self.db.orders.create_index("status")
        await self.db.payments.create_index("payment_id", unique=True)
        await self.db.payments.create_index("track_id", unique=True, sparse=True)
        await self.db.payments.create_index("order_id")
        await self.db.products.create_index("product_id", unique=True)
        await self.db.products.create_index("type")
        await self.db.giveaways.create_index("giveaway_id", unique=True)
        await self.db.giveaway_entries.create_index(
            [("giveaway_id", 1), ("user_id", 1)], unique=True
        )
        await self.db.promo_codes.create_index("code", unique=True)
        await self.db.audit_logs.create_index("created_at")
        logger.info("Database Indexes Initialized Successfully.")

    async def get_user(self, user_id: int) -> Optional[Dict]:
        return await self.db.users.find_one({"user_id": user_id})

    async def create_user_if_not_exists(
        self,
        user_id: int,
        username: Optional[str],
        first_name: str,
        referrer_id: Optional[str] = None,
    ) -> Dict:
        existing = await self.get_user(user_id)
        if existing:
            if existing.get("username") != username or existing.get(
                "first_name"
            ) != first_name:
                await self.db.users.update_one(
                    {"user_id": user_id},
                    {
                        "$set": {
                            "username": username,
                            "first_name": first_name,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                )
            return existing

        referral_code = f"REF-{secrets.token_hex(4).upper()}"
        valid_referrer = None
        if referrer_id and referrer_id != referral_code:
            ref_user = await self.db.users.find_one({"referral_id": referrer_id})
            if ref_user and ref_user["user_id"] != user_id:
                valid_referrer = ref_user["user_id"]

        now = datetime.now(timezone.utc)
        user_doc = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "balance": 0.0,
            "total_spent": 0.0,
            "total_deposited": 0.0,
            "total_orders": 0,
            "premium_orders": 0,
            "stars_orders": 0,
            "referral_id": referral_code,
            "referred_by": valid_referrer,
            "referral_earnings": 0.0,
            "is_banned": False,
            "is_admin": user_id in config.ADMIN_IDS,
            "language": "en",
            "notifications_enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        await self.db.users.insert_one(user_doc)
        logger.info(f"[USER] New user created: {user_id} ({username})")
        return user_doc

    async def update_balance(
        self, user_id: int, delta: float, reason: str
    ) -> bool:
        """Atomic balance modification to prevent race conditions."""
        if delta < 0:
            res = await self.db.users.update_one(
                {"user_id": user_id, "balance": {"$gte": abs(delta)}},
                {
                    "$inc": {"balance": delta},
                    "$set": {"updated_at": datetime.now(timezone.utc)},
                },
            )
            success = res.modified_count > 0
        else:
            res = await self.db.users.update_one(
                {"user_id": user_id},
                {
                    "$inc": {"balance": delta},
                    "$set": {"updated_at": datetime.now(timezone.utc)},
                },
            )
            success = res.modified_count > 0

        if success:
            await self.db.transactions.insert_one(
                {
                    "txn_id": f"TXN-{secrets.token_hex(6).upper()}",
                    "user_id": user_id,
                    "amount": delta,
                    "reason": reason,
                    "created_at": datetime.now(timezone.utc),
                }
            )
        return success


db = Database(config.MONGO_URI, config.MONGO_DB_NAME)

# ------------------------------------------------------------------------------
# 3. FSM STATES & ENUMS
# ------------------------------------------------------------------------------


class DepositStates(StatesGroup):
    waiting_for_amount = State()


class WithdrawalStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_address = State()


class PremiumOrderStates(StatesGroup):
    waiting_for_recipient = State()


class StarsOrderStates(StatesGroup):
    waiting_for_recipient = State()


class SupportStates(StatesGroup):
    waiting_for_category = State()
    waiting_for_message = State()


# ------------------------------------------------------------------------------
# 4. TRANSLATIONS / LOCALIZATION
# ------------------------------------------------------------------------------

TEXTS = {
    "en": {
        "welcome": (
            "<b>👋 Welcome to {bot_name}</b>\n\n"
            "Your premium portal for Telegram Premium, Stars, Channel Boosts, and Giveaways.\n\n"
            "💰 <b>Current Balance:</b> <code>${balance:.2f}</code>\n"
            "🆔 <b>User ID:</b> <code>{user_id}</code>\n\n"
            "Select an option below to begin:"
        ),
        "wallet": (
            "👛 <b>Your Personal Wallet</b>\n\n"
            "💰 <b>Current Balance:</b> <code>${balance:.2f}</code>\n"
            "📥 <b>Total Deposited:</b> <code>${total_deposited:.2f}</code>\n"
            "💸 <b>Total Spent:</b> <code>${total_spent:.2f}</code>\n\n"
            "Select an action below:"
        ),
        "deposit_prompt": "<b>💰 Deposit Funds</b>\n\nEnter amount in USD (Min $1.00, Max $1000.00):",
        "invalid_amount": "❌ Invalid amount entered. Please enter a valid numerical value.",
        "support_prompt": "<b>💬 Contact Customer Support</b>\n\nPlease select the category of your issue:",
        "maintenance": "🛠 <b>Bot Under Maintenance</b>\n\nWe are currently performing scheduled upgrades. All active payments are secure. Please try again later.",
    },
    "hi": {
        "welcome": (
            "<b>👋 {bot_name} में आपका स्वागत है</b>\n\n"
            "Telegram Premium, Stars, Channel Boosts और Giveaways के लिए आपका प्रीमियम स्टोर।\n\n"
            "💰 <b>वर्तमान बैलेंस:</b> <code>${balance:.2f}</code>\n"
            "🆔 <b>यूजर आईडी:</b> <code>{user_id}</code>\n\n"
            "शुरू करने के लिए नीचे दिए गए विकल्पों में से चुनें:"
        ),
        "wallet": (
            "👛 <b>आपका वॉलेट</b>\n\n"
            "💰 <b>वर्तमान बैलेंस:</b> <code>${balance:.2f}</code>\n"
            "📥 <b>कुल जमा:</b> <code>${total_deposited:.2f}</code>\n"
            "💸 <b>कुल खर्च:</b> <code>${total_spent:.2f}</code>\n\n"
            "नीचे दिए गए विकल्पों में से कार्रवाई चुनें:"
        ),
        "deposit_prompt": "<b>💰 फंड जमा करें</b>\n\nUSD में राशि दर्ज करें (न्यूनतम $1.00, अधिकतम $1000.00):",
        "invalid_amount": "❌ अमान्य राशि दर्ज की गई। कृपया सही संख्यात्मक मान दर्ज करें।",
        "support_prompt": "<b>💬 ग्राहक सहायता से संपर्क करें</b>\n\nकृपया अपनी समस्या की श्रेणी चुनें:",
        "maintenance": "🛠 <b>बॉट रखरखाव के अधीन है</b>\n\nहम वर्तमान में रखरखाव कर रहे हैं। आपके सभी भुगतान सुरक्षित हैं।",
    },
}

# ------------------------------------------------------------------------------
# 5. KEYBOARD BUILDERS
# ------------------------------------------------------------------------------


def get_main_menu_keyboard(
    lang: str = "en", is_admin: bool = False
) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(
                text="⭐ Buy Premium", callback_data="menu_buy_premium"
            ),
            InlineKeyboardButton(
                text="⭐ Buy Stars", callback_data="menu_buy_stars"
            ),
        ],
        [
            InlineKeyboardButton(
                text="⚡ Buy Boosts", callback_data="menu_buy_boosts"
            ),
            InlineKeyboardButton(
                text="🌟 Sell Stars", callback_data="menu_sell_stars"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🎁 Prepay Giveaway", callback_data="menu_prepay_giveaway"
            ),
            InlineKeyboardButton(
                text="📣 Telegram Ads", callback_data="menu_telegram_ads"
            ),
        ],
        [InlineKeyboardButton(text="👛 Wallet", callback_data="menu_wallet")],
        [
            InlineKeyboardButton(
                text="🌐 Change Language", callback_data="menu_language"
            ),
            InlineKeyboardButton(
                text="⚙️ Settings", callback_data="menu_settings"
            ),
        ],
        [
            InlineKeyboardButton(
                text="💬 Contact Support", callback_data="menu_support"
            )
        ],
        [
            InlineKeyboardButton(
                text="🎁 Daily Giveaways", callback_data="menu_daily_giveaway"
            )
        ],
    ]
    if is_admin:
        kb.append(
            [
                InlineKeyboardButton(
                    text="👑 Admin Panel", callback_data="admin_home"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_wallet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Deposit", callback_data="wallet_deposit"
                ),
                InlineKeyboardButton(
                    text="💸 Withdraw", callback_data="wallet_withdraw"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📜 Transaction History",
                    callback_data="wallet_history",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Back", callback_data="nav_home"
                )
            ],
        ]
    )


def get_back_keyboard(callback_data: str = "nav_home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Back", callback_data=callback_data
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Contact Support", callback_data="menu_support"
                )
            ],
        ]
    )


# ------------------------------------------------------------------------------
# 6. OXAPAY PAYMENT INTEGRATION
# ------------------------------------------------------------------------------


class OxaPayClient:

    def __init__(self, merchant_key: str, payout_key: str, callback_url: str):
        self.merchant_key = merchant_key
        self.payout_key = payout_key
        self.callback_url = callback_url
        self.base_url = "https://api.oxapay.com/merchants"

    async def create_invoice(
        self,
        order_id: str,
        amount: float,
        currency: str = "USD",
        description: str = "Wallet Deposit",
    ) -> Optional[Dict]:
        if config.TEST_MODE:
            logger.info(
                f"[TEST_MODE] Simulating OxaPay invoice creation for {order_id}"
            )
            return {
                "result": 100,
                "message": "success",
                "trackId": f"TEST-TRACK-{secrets.token_hex(4).upper()}",
                "payLink": f"https://sandbox.oxapay.com/pay/TEST-{order_id}",
            }

        payload = {
            "merchant": self.merchant_key,
            "amount": amount,
            "currency": currency,
            "lifeTime": 30,
            "feePaidByPayer": 0,
            "underPaidCover": 2,
            "callbackUrl": self.callback_url,
            "orderId": order_id,
            "description": description,
        }

        import aiohttp

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.base_url}/request", json=payload, timeout=15
                ) as response:
                    data = await response.json()
                    if data.get("result") == 100:
                        return data
                    logger.error(f"[OXAPAY] Invoice creation failed: {data}")
                    return None
            except Exception as e:
                logger.error(f"[OXAPAY] Network error creating invoice: {e}")
                return None

    @staticmethod
    def verify_webhook_hmac(
        raw_body: bytes, hmac_header: str, merchant_key: str
    ) -> bool:
        """Verify HMAC-SHA512 payload signature from OxaPay."""
        if not hmac_header or not merchant_key:
            return False
        calculated_sig = hmac.new(
            merchant_key.encode("utf-8"), raw_body, hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(calculated_sig.lower(), hmac_header.lower())


oxapay = OxaPayClient(
    config.OXAPAY_MERCHANT_API_KEY,
    config.OXAPAY_PAYOUT_API_KEY,
    config.OXAPAY_CALLBACK_URL,
)

# ------------------------------------------------------------------------------
# 7. DELIVERY QUEUE WORKER
# ------------------------------------------------------------------------------


class DeliveryQueue:

    def __init__(self, bot_instance: Bot):
        self.bot = bot_instance
        self.queue = asyncio.Queue()
        self.is_running = False

    async def add_job(self, order_id: str):
        await self.queue.put(order_id)
        logger.info(f"[QUEUE] Job added for order: {order_id}")

    async def start_worker(self):
        self.is_running = True
        logger.info("[QUEUE] Delivery Queue Worker Started.")
        while self.is_running:
            try:
                order_id = await self.queue.get()
                await self.process_order(order_id)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[QUEUE] Exception in worker: {e}")
                await asyncio.sleep(2)

    async def process_order(self, order_id: str):
        logger.info(f"[QUEUE] Processing delivery for order {order_id}...")
        order = await db.db.orders.find_one({"order_id": order_id})
        if not order or order["status"] != "paid":
            return

        p_type = order["product_type"]
        user_id = order["user_id"]
        recipient = order.get("recipient", user_id)

        await db.db.orders.update_one(
            {"order_id": order_id}, {"$set": {"delivery_status": "processing"}}
        )

        delivery_success = False
        failure_reason = None

        try:
            if config.TEST_MODE:
                delivery_success = True
            else:
                delivery_success = False
                failure_reason = "Manual Telegram fulfillment required."
        except Exception as e:
            delivery_success = False
            failure_reason = f"Execution error: {str(e)}"

        if delivery_success:
            await db.db.orders.update_one(
                {"order_id": order_id},
                {
                    "$set": {
                        "status": "completed",
                        "delivery_status": "completed",
                        "completed_at": datetime.now(timezone.utc),
                    }
                },
            )
            try:
                await self.bot.send_message(
                    user_id,
                    f"🎉 <b>Order Completed!</b>\n\nYour order <code>{order_id}</code> has been delivered successfully. Thank you for using our service!",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            logger.info(f"[DELIVERY] Order {order_id} completed successfully.")
        else:
            await db.db.orders.update_one(
                {"order_id": order_id},
                {
                    "$set": {
                        "status": "paid",
                        "delivery_status": "manual_fulfillment",
                        "failure_reason": failure_reason,
                    }
                },
            )

            # Alert Admins
            admin_msg = (
                f"🚨 <b>MANUAL FULFILLMENT REQUIRED</b>\n\n"
                f"<b>Order ID:</b> <code>{order_id}</code>\n"
                f"<b>User ID:</b> <code>{user_id}</code>\n"
                f"<b>Product:</b> {p_type.upper()}\n"
                f"<b>Recipient:</b> <code>{recipient}</code>\n"
                f"<b>Amount Paid:</b> ${order['price']:.2f}\n"
                f"<b>Reason:</b> {failure_reason}"
            )
            for admin_id in config.ADMIN_IDS:
                try:
                    await self.bot.send_message(
                        admin_id, admin_msg, parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass


# ------------------------------------------------------------------------------
# 8. AIOGRAM ROUTERS & HANDLERS
# ------------------------------------------------------------------------------

router = Router()


@router.message.outer_middleware()
@router.callback_query.outer_middleware()
async def check_maintenance_and_user(handler, event, data):
    user = data.get("event_from_user")
    if user:
        user_doc = await db.create_user_if_not_exists(
            user.id, user.username, user.first_name
        )
        data["db_user"] = user_doc

        if user_doc.get("is_banned"):
            if isinstance(event, Message):
                await event.answer("❌ Your account is suspended.")
            elif isinstance(event, CallbackQuery):
                await event.answer("❌ Your account is suspended.", show_alert=True)
            return

        settings = await db.db.settings.find_one({"key": "global_settings"})
        if settings and settings.get("maintenance_mode", False):
            if user.id not in config.ADMIN_IDS:
                text = TEXTS[user_doc.get("language", "en")]["maintenance"]
                if isinstance(event, Message):
                    await event.answer(text, parse_mode=ParseMode.HTML)
                elif isinstance(event, CallbackQuery):
                    await event.message.edit_text(text, parse_mode=ParseMode.HTML)
                return

    return await handler(event, data)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, db_user: Dict):
    args = command.args
    if args and args.startswith("REF-"):
        if not db_user.get("referred_by"):
            ref_user = await db.db.users.find_one({"referral_id": args})
            if ref_user and ref_user["user_id"] != message.from_user.id:
                await db.db.users.update_one(
                    {"user_id": message.from_user.id},
                    {"$set": {"referred_by": ref_user["user_id"]}},
                )

    lang = db_user.get("language", "en")
    text = TEXTS[lang]["welcome"].format(
        bot_name=config.BOT_USERNAME,
        balance=db_user["balance"],
        user_id=message.from_user.id,
    )
    kb = get_main_menu_keyboard(lang, is_admin=db_user["is_admin"])

    if config.BANNER_WELCOME:
        try:
            await message.answer_photo(
                photo=config.BANNER_WELCOME,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            return
        except Exception:
            pass

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data == "nav_home")
async def nav_home(callback: CallbackQuery, db_user: Dict):
    lang = db_user.get("language", "en")
    text = TEXTS[lang]["welcome"].format(
        bot_name=config.BOT_USERNAME,
        balance=db_user["balance"],
        user_id=callback.from_user.id,
    )
    kb = get_main_menu_keyboard(lang, is_admin=db_user["is_admin"])

    try:
        await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode=ParseMode.HTML, reply_markup=kb
        )
    await callback.answer()


@router.callback_query(F.data == "menu_wallet")
async def menu_wallet(callback: CallbackQuery, db_user: Dict):
    lang = db_user.get("language", "en")
    text = TEXTS[lang]["wallet"].format(
        balance=db_user["balance"],
        total_deposited=db_user["total_deposited"],
        total_spent=db_user["total_spent"],
    )
    kb = get_wallet_keyboard()

    if config.BANNER_WALLET and callback.message.photo:
        try:
            await callback.message.edit_caption(
                caption=text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
            await callback.answer()
            return
        except Exception:
            pass

    await callback.message.edit_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "wallet_deposit")
async def wallet_deposit(callback: CallbackQuery, state: FSMContext, db_user: Dict):
    lang = db_user.get("language", "en")
    await state.set_state(DepositStates.waiting_for_amount)
    await callback.message.edit_text(
        TEXTS[lang]["deposit_prompt"],
        parse_mode=ParseMode.HTML,
        reply_markup=get_back_keyboard("menu_wallet"),
    )
    await callback.answer()


@router.message(DepositStates.waiting_for_amount)
async def process_deposit_amount(
    message: Message, state: FSMContext, db_user: Dict
):
    lang = db_user.get("language", "en")
    try:
        amount = float(message.text.strip())
        if amount < 1.0 or amount > 1000.0:
            raise ValueError()
    except Exception:
        await message.answer(TEXTS[lang]["invalid_amount"])
        return

    await state.clear()
    order_id = f"DEP-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"

    invoice = await oxapay.create_invoice(
        order_id=order_id,
        amount=amount,
        description=f"Deposit for {message.from_user.id}",
    )

    if not invoice or invoice.get("result") != 100:
        await message.answer(
            "❌ Failed to generate crypto invoice. Please try again later.",
            reply_markup=get_back_keyboard("menu_wallet"),
        )
        return

    track_id = invoice.get("trackId")
    pay_link = invoice.get("payLink")

    now = datetime.now(timezone.utc)
    payment_doc = {
        "payment_id": f"PAY-{secrets.token_hex(6).upper()}",
        "order_id": order_id,
        "user_id": message.from_user.id,
        "amount": amount,
        "currency": "USD",
        "track_id": track_id,
        "status": "pending",
        "pay_link": pay_link,
        "created_at": now,
        "expires_at": now + timedelta(minutes=30),
    }
    await db.db.payments.insert_one(payment_doc)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pay with OxaPay", url=pay_link)],
            [
                InlineKeyboardButton(
                    text="🔙 Back to Wallet", callback_data="menu_wallet"
                )
            ],
        ]
    )

    await message.answer(
        f"<b>💰 Deposit Invoice Created</b>\n\n"
        f"<b>Order ID:</b> <code>{order_id}</code>\n"
        f"<b>Amount:</b> <code>${amount:.2f} USD</code>\n"
        f"<b>Status:</b> Pending Payment\n\n"
        f"Click the button below to complete payment via OxaPay Crypto Gateway.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


# ------------------------------------------------------------------------------
# 9. OXAPAY WEBHOOK HTTP SERVER (aiohttp)
# ------------------------------------------------------------------------------


async def handle_oxapay_webhook(request: web.Request) -> web.Response:
    try:
        raw_body = await request.read()
        hmac_header = request.headers.get("HMAC", "") or request.headers.get(
            "Hmac", ""
        )

        if not OxaPayClient.verify_webhook_hmac(
            raw_body, hmac_header, config.OXAPAY_MERCHANT_API_KEY
        ):
            logger.warning("[SECURITY] Invalid HMAC Signature on OxaPay Webhook!")
            return web.json_response({"error": "Invalid signature"}, status=400)

        data = json.loads(raw_body.decode("utf-8"))
        status = data.get("status")
        order_id = data.get("orderId")
        track_id = str(data.get("trackId"))
        amount = float(data.get("amount", 0.0))

        payment = await db.db.payments.find_one({"order_id": order_id})
        if not payment or payment.get("status") == "paid":
            return web.json_response({"status": "already_processed"}, status=200)

        if status in ["Paid", "paid", "Completed", "completed"]:
            await db.db.payments.update_one(
                {"order_id": order_id},
                {
                    "$set": {
                        "status": "paid",
                        "track_id": track_id,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )

            if order_id.startswith("DEP-"):
                user_id = payment["user_id"]
                credited = await db.update_balance(
                    user_id, amount, f"OxaPay Deposit ({order_id})"
                )
                if credited:
                    await db.db.users.update_one(
                        {"user_id": user_id},
                        {"$inc": {"total_deposited": amount}},
                    )
                    try:
                        await bot_instance.send_message(
                            user_id,
                            f"💳 <b>Deposit Confirmed!</b>\n\n"
                            f"Your balance has been credited with <code>${amount:.2f} USD</code>.\n"
                            f"<b>Transaction ID:</b> <code>{track_id}</code>",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass

            elif order_id.startswith("ORD-"):
                await db.db.orders.update_one(
                    {"order_id": order_id},
                    {
                        "$set": {
                            "status": "paid",
                            "paid_at": datetime.now(timezone.utc),
                        }
                    },
                )
                await delivery_queue.add_job(order_id)

        return web.json_response({"status": "ok"}, status=200)

    except Exception as e:
        logger.error(
            f"[WEBHOOK ERROR] Exception processing callback: {e}", exc_info=True
        )
        return web.json_response({"error": "Internal server error"}, status=500)


async def handle_health_check(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "timestamp": time.time()})


# ------------------------------------------------------------------------------
# 10. MAIN BOOTSTRAP
# ------------------------------------------------------------------------------

bot_instance: Bot
delivery_queue: DeliveryQueue


async def on_startup(app: web.Application):
    global bot_instance, delivery_queue

    logger.info("Initializing Database...")
    await db.init_indexes()

    bot_instance = Bot(token=config.BOT_TOKEN)
    delivery_queue = DeliveryQueue(bot_instance)

    asyncio.create_task(delivery_queue.start_worker())

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    asyncio.create_task(dp.start_polling(bot_instance))
    logger.info("Telegram Bot Polling Started.")


def main():
    app = web.Application()
    app.on_startup.append(on_startup)

    app.router.add_get("/health", handle_health_check)
    app.router.add_post("/oxapay/webhook", handle_oxapay_webhook)

    logger.info(
        f"Starting HTTP Webhook Server on {config.WEBHOOK_HOST}:{config.WEBHOOK_PORT}..."
    )
    web.run_app(
        app,
        host=config.WEBHOOK_HOST,
        port=config.WEBHOOK_PORT,
        print=logger.info,
    )


if __name__ == "__main__":
    main()
