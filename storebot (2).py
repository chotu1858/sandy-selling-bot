#!/usr/bin/env python3
"""
SANDY STORE - TELEGRAM BOT  (v6 - VIP store)

v6: welcome media fix (albums, GIFs, files), broadcast with media + buttons, Proof button + proof channel
auto-post (anonymous verified buyers + Buy Now), payment reminder, professional maintenance screen,
new Mini App pay page with UPI app deep links.

v5: products are buttons right on the main page (no Store button); every product has its own VIP link,
demo link, button titles, multiple photos/videos, demo photos/videos and plan durations (all editable
from the admin panel). Welcome text + multiple welcome photos/videos, UPI ID, store name, support
username, recharge limits and referral % are editable from the admin panel too.
Leaderboard and total-revenue views were removed.

v3: every message in bold, every button coloured + bold, maintenance mode,
leaderboard, help/FAQ, custom welcome message, deduct balance, DB backup,
admin logs, coupon/plan management, top products/buyers, banned list.

What's new vs v1
- Colourful buttons (Telegram Bot API 9.4 `style`: primary / success / danger)
- Secrets moved to .env (no tokens inside the code)
- Bug fixes: referral commission, wallet history, coupon discounts,
  double-tap double-charge, deleted products coming back after restart,
  wrong "today" sales (timezone), key refund + key delivered at same time
- Ban / unban, user search, multiple admins
- Duplicate UTR protection, auto-expire unfinished payments
- Product edit (price), product on/off, delete confirmation
- Sales report + CSV export
- Flood protection, admin action log
"""

import os
import io
import re
import sys
import csv
import json
import html
import time
import random
import string
import sqlite3
import base64
import hmac
import hashlib
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote, urlparse, parse_qs

import requests
import qrcode

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile,
    InputMediaPhoto, InputMediaVideo, WebAppInfo
)
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ExtBot, Defaults
)

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION  (values come from .env)
# ═══════════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env(path: str):
    """Tiny .env loader (no extra package needed)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(BASE_DIR, ".env"))

# ┌──────────────────────────────────────────────────────────────┐
# │  🔧 YAHAN APNI SETTINGS DALO  (ya .env file use karo)        │
# └──────────────────────────────────────────────────────────────┘
BOT_TOKEN_VALUE = ""                  # ← Telegram Bot Token (@BotFather se)
ADMIN_ID_VALUE = "8037099066"         # ← Admin Telegram ID  (multiple: "111,222")
FMPAY_API_KEY_VALUE = "fmpay_eecdb330d14f8aa8d735a0d5ec87c115d8963d1d"   # ← FMPay API key
FMPAY_BASE_URL_VALUE = "https://famapi-sas5.onrender.com"            # ← FMPay API URL
UPI_ID_VALUE = "harshai83@fam"        # ← Paisa receive karne wali UPI ID (dobara check karo!)
# ────────────────────────────────────────────────────────────────

BOT_TOKEN = BOT_TOKEN_VALUE.strip() or os.getenv("BOT_TOKEN", "").strip()
_admin_raw = ADMIN_ID_VALUE.strip() or os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.isdigit()}
FMPAY_API_KEY = FMPAY_API_KEY_VALUE.strip() or os.getenv("FMPAY_API_KEY", "").strip()
FMPAY_BASE_URL = (FMPAY_BASE_URL_VALUE.strip() or os.getenv("FMPAY_BASE_URL", "")).rstrip("/")
FMPAY_TIMEOUT = 60   # free Render servers can take ~50s to wake up
UPI_ID = UPI_ID_VALUE.strip() or os.getenv("UPI_ID", "").strip()
STORE_NAME = os.getenv("STORE_NAME", "SANDY_STORE01")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@SANDY_STORE01")

API_URL = os.getenv("API_URL", "https://fflevel.in/api/external/panel/keygen")
API_HEADERS = {
    "X-API-Key": os.getenv("API_KEY", ""),
    "M-Key": os.getenv("API_MKEY", ""),
}

MIN_RECHARGE = float(os.getenv("MIN_RECHARGE", "1"))
MAX_RECHARGE = float(os.getenv("MAX_RECHARGE", "10000"))
REFERRAL_PERCENT = float(os.getenv("REFERRAL_PERCENT", "2"))
PAYMENT_EXPIRY_MIN = int(os.getenv("PAYMENT_EXPIRY_MIN", "30"))
TZ_OFFSET_MIN = int(os.getenv("TZ_OFFSET_MIN", "330"))  # IST = +5:30

DB_NAME = os.path.join(BASE_DIR, os.getenv("DB_NAME", "SHADMAN_store.db"))

# ─── Mini App payment page (opens inside Telegram when the user taps "Pay Now") ───
# WEBAPP_URL = public HTTPS address of this bot's web server, e.g. https://pay.yourdomain.com
# (set it here, in .env, or from Admin Panel -> Settings -> Pay Page URL). Empty = classic QR photo.
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))
PAY_MODE = "auto"   # "auto" = gateway verifies the UTR | "manual" = admin approves every payment
PROOF_LINK = ""     # public proof channel link shown to users (set in Admin -> Proof)
PROOF_CHAT = ""     # chat id / @username of the channel the bot posts proofs into
REMIND_AFTER_MIN = int(os.getenv("REMIND_AFTER_MIN", "5"))   # unpaid payment reminder after N minutes

# defaults from the code/.env - values changed in the admin panel (stored in the DB) override these
_DEFAULT_CFG = {
    "UPI_ID": UPI_ID, "STORE_NAME": STORE_NAME, "SUPPORT_USERNAME": SUPPORT_USERNAME,
    "MIN_RECHARGE": MIN_RECHARGE, "MAX_RECHARGE": MAX_RECHARGE, "REFERRAL_PERCENT": REFERRAL_PERCENT,
    "FMPAY_API_KEY": FMPAY_API_KEY, "WEBAPP_URL": WEBAPP_URL,
    "PROOF_LINK": "", "PROOF_CHAT": "",
}
MAX_MEDIA_PER_ITEM = 20   # max photos+videos for the welcome screen / a product / a demo

API_HOURS_MAP = {
    "1 Hour": 1, "3 Hours": 3, "6 Hours": 6, "12 Hours": 12, "1 Day": 24,
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# FMPAY CLIENT  (UTR verification:  POST /verify  {utr, amount})
# ═══════════════════════════════════════════════════════════════════

class FmPayError(Exception):
    pass


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmpay_verify(utr: str, amount: float) -> Dict[str, Any]:
    """Asks the gateway if a payment with this UTR and amount was received.
    -> {'ok': bool, 'message': str, 'amount': float|None}.  Raises FmPayError on gateway problems."""
    try:
        resp = requests.post(
            f"{FMPAY_BASE_URL}/verify",
            json={"utr": utr, "amount": round(float(amount), 2), "api_key": FMPAY_API_KEY},
            headers={"X-API-Key": FMPAY_API_KEY, "Content-Type": "application/json"},
            timeout=FMPAY_TIMEOUT)
    except requests.RequestException as e:
        raise FmPayError(f"network error: {e}")
    try:
        j = resp.json()
    except ValueError:
        raise FmPayError(f"non-JSON reply (HTTP {resp.status_code})")
    if not isinstance(j, dict):
        raise FmPayError("unexpected reply")
    logger.info(f"FMPay verify {utr}: HTTP {resp.status_code} {str(j)[:300]}")
    msg = str(j.get("message") or j.get("error") or j.get("status") or "")
    if resp.status_code in (401, 403) or resp.status_code >= 500:
        raise FmPayError(msg or f"HTTP {resp.status_code}")
    ok = j.get("success") is True
    for k in ("verified", "found", "paid", "valid"):
        if k in j and not j[k]:
            ok = False
    if str(j.get("status") or "").lower() in ("failed", "not_found", "notfound", "pending", "invalid", "error"):
        ok = False
    paid = _to_float(j.get("amount"))
    if ok and paid is not None and paid + 0.011 < float(amount):
        ok, msg = False, "Amount mismatch"
    return {"ok": ok, "message": msg, "amount": paid}


# ═══════════════════════════════════════════════════════════════════
# DATABASE LAYER
# ═══════════════════════════════════════════════════════════════════

class Database:
    def __init__(self, name: str):
        self.name = name
        self.init_db()
        self.migrate()
        self.seed_default_data()

    def _connect(self):
        conn = sqlite3.connect(self.name, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _run(self, sql: str, params=(), fetch: Optional[str] = None):
        """fetch: 'one' | 'all' | 'id' (lastrowid) | None (rowcount)."""
        conn = self._connect()
        try:
            cur = conn.execute(sql, params)
            if fetch == "one":
                row = cur.fetchone()
                return dict(row) if row else None
            if fetch == "all":
                return [dict(r) for r in cur.fetchall()]
            conn.commit()
            if fetch == "id":
                return cur.lastrowid
            return cur.rowcount
        finally:
            conn.close()

    # ─── schema ───
    def init_db(self):
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                balance DECIMAL(10,2) DEFAULT 0,
                referral_code TEXT UNIQUE,
                referred_by INTEGER,
                referral_earnings DECIMAL(10,2) DEFAULT 0,
                total_orders INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                active_coupon TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                description TEXT,
                image_file_id TEXT,
                status TEXT DEFAULT 'active',
                use_api INTEGER DEFAULT 1,
                api_version TEXT DEFAULT 'v2',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS product_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                plan_name TEXT NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                api_hours INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                plan_name TEXT NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                key_code TEXT,
                android_id TEXT,
                api_response TEXT,
                status TEXT DEFAULT 'completed',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount DECIMAL(10,2) NOT NULL,
                utr TEXT,
                screenshot_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL,
                value DECIMAL(10,2) NOT NULL,
                expiry_date TIMESTAMP,
                usage_limit INTEGER DEFAULT 1,
                used_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS coupon_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coupon_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (coupon_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_media_owner ON media(owner);
        """)
        conn.commit()
        conn.close()

    def migrate(self):
        """Adds new columns to databases created by the old version."""
        conn = self._connect()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
            if "banned" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
            if "active_coupon" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN active_coupon TEXT")
            tcols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
            for col, ddl in (("order_id", "TEXT"), ("payable_amount", "DECIMAL(10,2)"),
                             ("chat_id", "BIGINT"), ("message_id", "INTEGER"),
                             ("reminded", "INTEGER DEFAULT 0")):
                if col not in tcols:
                    conn.execute(f"ALTER TABLE transactions ADD COLUMN {col} {ddl}")
            pcols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
            for col in ("vip_link", "vip_title", "demo_link", "demo_title"):
                if col not in pcols:
                    conn.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT")
            ocols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
            for col in ("vip_link", "expires_at"):
                if col not in ocols:
                    conn.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_order ON transactions(order_id)")
            try:  # a UTR can be credited only once
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_utr_approved "
                             "ON transactions(utr) WHERE utr IS NOT NULL AND status = 'approved'")
            except sqlite3.DatabaseError as e:
                logger.warning(f"Could not create unique UTR index: {e}")
            conn.commit()
        finally:
            conn.close()

    def seed_default_data(self):
        """Only seeds a demo product on a completely empty store."""
        row = self._run("SELECT COUNT(*) AS n FROM products", fetch="one")
        if row and row["n"] > 0:
            return
        pid = self.create_product(
            "BALA MOD MENU", "🎮 Gaming",
            "⚡ Fast Delivery\n🔒 Secure License Access\n💎 Reliable Service",
        )
        for name, price, hours in [("1 Hour", 25, 1), ("3 Hours", 55, 3), ("6 Hours", 95, 6),
                                   ("12 Hours", 190, 12), ("1 Day", 290, 24)]:
            self.add_product_plan(pid, name, price, hours)

    # ─── users ───
    def get_user(self, telegram_id: int) -> Optional[Dict]:
        return self._run("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,), "one")

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        return self._run("SELECT * FROM users WHERE id = ?", (user_id,), "one")

    def get_user_by_ref(self, code: str) -> Optional[Dict]:
        return self._run("SELECT * FROM users WHERE referral_code = ?", (code,), "one")

    def find_user(self, q: str) -> Optional[Dict]:
        q = q.strip().lstrip("@")
        if q.isdigit():
            return self.get_user(int(q))
        return self._run("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (q,), "one")

    def create_user(self, telegram_id, username, first_name, referred_by=None):
        code = "SANDY" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        try:
            self._run(
                "INSERT INTO users (telegram_id, username, first_name, referral_code, referred_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (telegram_id, username, first_name, code, referred_by),
            )
        except sqlite3.IntegrityError:
            pass

    def update_user_info(self, telegram_id, username, first_name):
        self._run("UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?",
                  (username, first_name, telegram_id))

    def update_balance(self, telegram_id: int, amount: float):
        self._run("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amount, telegram_id))

    def deduct_balance(self, telegram_id: int, amount: float) -> bool:
        """Atomic: only deducts if balance is enough."""
        n = self._run(
            "UPDATE users SET balance = balance - ? WHERE telegram_id = ? AND balance >= ? - 0.001",
            (amount, telegram_id, amount),
        )
        return n > 0

    def credit_referral(self, telegram_id: int, amount: float):
        self._run(
            "UPDATE users SET balance = balance + ?, referral_earnings = referral_earnings + ? "
            "WHERE telegram_id = ?", (amount, amount, telegram_id))

    def increment_orders(self, telegram_id: int):
        self._run("UPDATE users SET total_orders = total_orders + 1 WHERE telegram_id = ?", (telegram_id,))

    def set_banned(self, telegram_id: int, banned: bool):
        self._run("UPDATE users SET banned = ? WHERE telegram_id = ?", (1 if banned else 0, telegram_id))

    def set_active_coupon(self, telegram_id: int, code: Optional[str]):
        self._run("UPDATE users SET active_coupon = ? WHERE telegram_id = ?", (code, telegram_id))

    def get_all_users(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        return self._run("SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                         (limit, offset), "all")

    def count_users(self) -> int:
        return self._run("SELECT COUNT(*) AS n FROM users", fetch="one")["n"]

    # ─── products ───
    def get_products(self, group: Optional[str] = None, include_inactive: bool = False) -> List[Dict]:
        sql = "SELECT * FROM products WHERE 1=1"
        params: list = []
        if not include_inactive:
            sql += " AND status = 'active'"
        if group:
            sql += " AND group_name = ?"
            params.append(group)
        return self._run(sql + " ORDER BY id", tuple(params), "all")

    def get_product(self, product_id: int) -> Optional[Dict]:
        return self._run("SELECT * FROM products WHERE id = ?", (product_id,), "one")

    def get_product_plans(self, product_id: int) -> List[Dict]:
        return self._run("SELECT * FROM product_plans WHERE product_id = ? ORDER BY price", (product_id,), "all")

    def get_plan(self, plan_id: int) -> Optional[Dict]:
        return self._run("SELECT * FROM product_plans WHERE id = ?", (plan_id,), "one")

    def create_product(self, name, group, description, use_api=1, api_version="v2",
                       vip_link=None, demo_link=None) -> int:
        return self._run(
            "INSERT INTO products (name, group_name, description, use_api, api_version, vip_link, demo_link) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, group, description, use_api, api_version, vip_link, demo_link), "id")

    def add_product_plan(self, product_id, plan_name, price, api_hours=None):
        self._run("INSERT INTO product_plans (product_id, plan_name, price, api_hours) VALUES (?, ?, ?, ?)",
                  (product_id, plan_name, price, api_hours))

    def update_plan_price(self, plan_id: int, price: float):
        self._run("UPDATE product_plans SET price = ? WHERE id = ?", (price, plan_id))

    def update_plan_name(self, plan_id: int, name: str, hours: int):
        self._run("UPDATE product_plans SET plan_name = ?, api_hours = ? WHERE id = ?", (name, hours, plan_id))

    PRODUCT_COLUMNS = ("name", "description", "vip_link", "vip_title", "demo_link", "demo_title")

    def update_product_field(self, product_id: int, column: str, value: Optional[str]):
        if column not in self.PRODUCT_COLUMNS:
            raise ValueError(f"column not allowed: {column}")
        self._run(f"UPDATE products SET {column} = ? WHERE id = ?", (value, product_id))

    def set_product_api(self, product_id: int, use_api: int):
        self._run("UPDATE products SET use_api = ? WHERE id = ?", (1 if use_api else 0, product_id))

    def set_product_status(self, product_id: int, status: str):
        self._run("UPDATE products SET status = ? WHERE id = ?", (status, product_id))

    def delete_product(self, product_id: int):
        self._run("DELETE FROM product_plans WHERE product_id = ?", (product_id,))
        self._run("DELETE FROM media WHERE owner LIKE ?", (f"p{product_id}:%",))
        self._run("DELETE FROM products WHERE id = ?", (product_id,))

    def get_product_groups(self) -> List[str]:
        rows = self._run("SELECT DISTINCT group_name FROM products WHERE status = 'active'", fetch="all")
        return [r["group_name"] for r in rows]

    # ─── media (photos / videos for the welcome screen, products and demos) ───
    def add_media(self, owner: str, file_id: str, media_type: str):
        self._run("INSERT INTO media (owner, file_id, media_type) VALUES (?, ?, ?)",
                  (owner, file_id, media_type))

    def get_media(self, owner: str) -> List[Dict]:
        return self._run("SELECT * FROM media WHERE owner = ? ORDER BY id", (owner,), "all")

    def count_media(self, owner: str) -> int:
        return self._run("SELECT COUNT(*) AS n FROM media WHERE owner = ?", (owner,), "one")["n"]

    def clear_media(self, owner: str):
        self._run("DELETE FROM media WHERE owner = ?", (owner,))

    # ─── orders ───
    def create_order(self, order_id, user_id, product_id, plan_id, plan_name, price,
                     key_code=None, android_id=None, api_response=None, vip_link=None, expires_at=None):
        self._run(
            "INSERT INTO orders (order_id, user_id, product_id, plan_id, plan_name, price, key_code, android_id, "
            "api_response, vip_link, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, user_id, product_id, plan_id, plan_name, price, key_code, android_id, api_response,
             vip_link, expires_at))

    def get_user_orders(self, user_id: int, limit: int = 10) -> List[Dict]:
        return self._run(
            "SELECT o.*, COALESCE(p.name, 'Removed product') AS product_name "
            "FROM orders o LEFT JOIN products p ON o.product_id = p.id "
            "WHERE o.user_id = ? ORDER BY o.id DESC LIMIT ?", (user_id, limit), "all")

    def get_all_orders(self, limit: int = 50) -> List[Dict]:
        return self._run(
            "SELECT o.*, COALESCE(p.name, 'Removed product') AS product_name, u.username, u.telegram_id "
            "FROM orders o LEFT JOIN products p ON o.product_id = p.id "
            "JOIN users u ON o.user_id = u.id ORDER BY o.id DESC LIMIT ?", (limit,), "all")

    def count_orders(self) -> int:
        return self._run("SELECT COUNT(*) AS n FROM orders WHERE status = 'completed'", fetch="one")["n"]

    def get_total_revenue(self) -> float:
        r = self._run("SELECT COALESCE(SUM(price), 0) AS s FROM orders WHERE status = 'completed'", fetch="one")
        return float(r["s"])

    def sales_today(self):
        mod = f"{TZ_OFFSET_MIN:+d} minutes"
        r = self._run(
            "SELECT COUNT(*) AS c, COALESCE(SUM(price), 0) AS s FROM orders "
            "WHERE status = 'completed' AND DATE(created_at, ?) = DATE('now', ?)", (mod, mod), "one")
        return r["c"], float(r["s"])

    def sales_last_days(self, days: int):
        r = self._run(
            "SELECT COUNT(*) AS c, COALESCE(SUM(price), 0) AS s FROM orders "
            "WHERE status = 'completed' AND created_at >= datetime('now', ?)", (f"-{days} days",), "one")
        return r["c"], float(r["s"])

    # ─── transactions (auto-verified UPI payments) ───
    def create_payment_tx(self, user_id: int, amount: float, order_id: str,
                          payable: float, chat_id: int) -> int:
        return self._run(
            "INSERT INTO transactions (user_id, type, amount, order_id, payable_amount, chat_id) "
            "VALUES (?, 'recharge', ?, ?, ?, ?)", (user_id, amount, order_id, payable, chat_id), "id")

    def set_tx_message(self, tx_id: int, message_id: int):
        self._run("UPDATE transactions SET message_id = ? WHERE id = ?", (message_id, tx_id))

    def get_transaction(self, tx_id: int) -> Optional[Dict]:
        return self._run("SELECT * FROM transactions WHERE id = ?", (tx_id,), "one")

    def get_tx_by_order(self, order_id: str) -> Optional[Dict]:
        return self._run("SELECT * FROM transactions WHERE order_id = ?", (order_id,), "one")

    def get_user_transactions(self, user_id: int, limit: int = 10) -> List[Dict]:
        return self._run("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                         (user_id, limit), "all")

    def get_pending_transactions(self) -> List[Dict]:
        """Payments waiting for the user to pay (auto-checked by the poll job)."""
        return self._run(
            "SELECT t.*, u.telegram_id, u.username FROM transactions t JOIN users u ON t.user_id = u.id "
            "WHERE t.status = 'pending' AND t.order_id IS NOT NULL ORDER BY t.id", fetch="all")

    def get_due_reminders(self, after_min: int, expiry_min: int) -> List[Dict]:
        """Unpaid payment sessions older than `after_min` that have not been reminded yet."""
        return self._run(
            "SELECT t.*, u.telegram_id, u.banned FROM transactions t JOIN users u ON t.user_id = u.id "
            "WHERE t.status = 'pending' AND t.order_id IS NOT NULL AND COALESCE(t.reminded, 0) = 0 "
            "AND t.created_at <= datetime('now', ?) AND t.created_at > datetime('now', ?) ORDER BY t.id",
            (f"-{after_min} minutes", f"-{expiry_min} minutes"), "all")

    def mark_reminded(self, tx_id: int):
        self._run("UPDATE transactions SET reminded = 1 WHERE id = ?", (tx_id,))

    def get_total_pending_amount(self) -> float:
        r = self._run("SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
                      "WHERE status = 'pending' AND order_id IS NOT NULL", fetch="one")
        return float(r["s"])

    def count_open_payments(self, user_id: int, minutes: int = 10) -> int:
        r = self._run(
            "SELECT COUNT(*) AS n FROM transactions WHERE user_id = ? AND status = 'pending' "
            "AND order_id IS NOT NULL AND created_at >= datetime('now', ?)",
            (user_id, f"-{minutes} minutes"), "one")
        return int(r["n"])

    def utr_registered(self, utr: str) -> bool:
        return self._run("SELECT id FROM transactions WHERE UPPER(utr) = UPPER(?) AND status = 'approved'",
                         (utr,), "one") is not None

    def set_tx_review(self, tx_id: int, utr: str) -> bool:
        """Manual mode: the user sent a UTR, now the admin has to approve it."""
        return self._run("UPDATE transactions SET status = 'review', utr = ? "
                         "WHERE id = ? AND status IN ('pending', 'expired')", (utr, tx_id)) > 0

    def set_tx_status(self, tx_id: int, status: str, allowed=("pending",)) -> bool:
        marks = ",".join("?" * len(allowed))
        return self._run(f"UPDATE transactions SET status = ? WHERE id = ? AND status IN ({marks})",
                         (status, tx_id, *allowed)) > 0

    def credit_payment(self, tx_id: int, utr: Optional[str]) -> str:
        """Atomic: registers the UTR and credits the wallet exactly once.
        Returns: ok | done (already credited) | duplicate (UTR already used) | invalid"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
            if not tx:
                conn.rollback()
                return "invalid"
            if tx["status"] == "approved":
                conn.rollback()
                return "done"
            if tx["status"] not in ("pending", "expired", "review"):
                conn.rollback()
                return "invalid"
            if utr and conn.execute("SELECT 1 FROM transactions WHERE UPPER(utr) = UPPER(?) AND status = 'approved'",
                                    (utr,)).fetchone():
                conn.execute("UPDATE transactions SET status = 'duplicate' WHERE id = ?", (tx_id,))
                conn.commit()
                return "duplicate"
            conn.execute("UPDATE transactions SET status = 'approved', utr = ? WHERE id = ?",
                         (utr or None, tx_id))
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?",
                         (float(tx["amount"]), tx["user_id"]))
            conn.commit()
            return "ok"
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def expire_stale(self, minutes: int) -> int:
        """Payment sessions that stayed unpaid for too long."""
        return self._run(
            "UPDATE transactions SET status = 'expired' "
            "WHERE status = 'pending' AND order_id IS NOT NULL AND created_at < datetime('now', ?)",
            (f"-{minutes} minutes",))

    # ─── coupons ───
    def get_coupon(self, code: str) -> Optional[Dict]:
        return self._run("SELECT * FROM coupons WHERE code = ?", (code.upper(),), "one")

    def redeem_coupon(self, code: str, user_id: int):
        """Returns (status, coupon). status: invalid | used | limit | ok."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM coupons WHERE code = ? AND (expiry_date IS NULL OR expiry_date > datetime('now'))",
                (code.upper(),)).fetchone()
            if not row:
                return "invalid", None
            c = dict(row)
            if conn.execute("SELECT 1 FROM coupon_uses WHERE coupon_id = ? AND user_id = ?",
                            (c["id"], user_id)).fetchone():
                return "used", c
            cur = conn.execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE id = ? AND used_count < usage_limit",
                (c["id"],))
            if cur.rowcount == 0:
                return "limit", c
            conn.execute("INSERT INTO coupon_uses (coupon_id, user_id) VALUES (?, ?)", (c["id"], user_id))
            conn.commit()
            return "ok", c
        finally:
            conn.close()

    def create_coupon(self, code, type_, value, expiry_days=None, limit=1):
        expiry = None
        if expiry_days:
            expiry = (datetime.now(timezone.utc) + timedelta(days=expiry_days)).strftime("%Y-%m-%d %H:%M:%S")
        self._run("INSERT INTO coupons (code, type, value, expiry_date, usage_limit) VALUES (?, ?, ?, ?, ?)",
                  (code.upper(), type_, value, expiry, limit))

    def get_all_coupons(self) -> List[Dict]:
        return self._run("SELECT * FROM coupons ORDER BY id DESC LIMIT 30", fetch="all")

    def delete_coupon(self, coupon_id: int):
        self._run("DELETE FROM coupons WHERE id = ?", (coupon_id,))

    def delete_plan(self, plan_id: int):
        self._run("DELETE FROM product_plans WHERE id = ?", (plan_id,))

    # ─── admin log ───
    def log_admin(self, admin_id: int, action: str):
        logger.info(f"ADMIN {admin_id}: {action}")
        self._run("INSERT INTO admin_logs (admin_id, action) VALUES (?, ?)", (admin_id, action))

    def get_logs(self, limit: int = 15) -> List[Dict]:
        return self._run("SELECT * FROM admin_logs ORDER BY id DESC LIMIT ?", (limit,), "all")

    # ─── settings ───
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        r = self._run("SELECT value FROM settings WHERE key = ?", (key,), "one")
        return r["value"] if r and r["value"] is not None else default

    def set_setting(self, key: str, value: Optional[str]):
        if value is None:
            self._run("DELETE FROM settings WHERE key = ?", (key,))
        else:
            self._run("INSERT INTO settings (key, value) VALUES (?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # ─── stats / leaderboards ───
    def get_top_buyers(self, limit: int = 10) -> List[Dict]:
        return self._run(
            "SELECT u.*, SUM(o.price) AS spent, COUNT(o.id) AS cnt FROM users u "
            "JOIN orders o ON o.user_id = u.id AND o.status = 'completed' "
            "GROUP BY u.id ORDER BY spent DESC LIMIT ?", (limit,), "all")

    def get_top_referrers(self, limit: int = 10) -> List[Dict]:
        return self._run(
            "SELECT * FROM (SELECT u.*, (SELECT COUNT(*) FROM users r WHERE r.referred_by = u.id) AS refs "
            "FROM users u) WHERE refs > 0 ORDER BY refs DESC, referral_earnings DESC LIMIT ?", (limit,), "all")

    def get_top_products(self, limit: int = 5) -> List[Dict]:
        return self._run(
            "SELECT COALESCE(p.name, 'Removed product') AS name, COUNT(o.id) AS cnt, SUM(o.price) AS total "
            "FROM orders o LEFT JOIN products p ON o.product_id = p.id WHERE o.status = 'completed' "
            "GROUP BY o.product_id ORDER BY total DESC LIMIT ?", (limit,), "all")

    def get_banned_users(self) -> List[Dict]:
        return self._run("SELECT * FROM users WHERE banned = 1 ORDER BY id DESC LIMIT 30", fetch="all")

    def new_users_today(self) -> int:
        mod = f"{TZ_OFFSET_MIN:+d} minutes"
        return self._run("SELECT COUNT(*) AS n FROM users WHERE DATE(created_at, ?) = DATE('now', ?)",
                         (mod, mod), "one")["n"]

    def total_wallet_balance(self) -> float:
        return float(self._run("SELECT COALESCE(SUM(balance), 0) AS s FROM users", fetch="one")["s"])

    def backup_to(self, path: str):
        src = self._connect()
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()


db = Database(DB_NAME)


# ═══════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════

esc = html.escape


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def generate_order_id() -> str:
    return "".join(random.choices(string.digits, k=8))


def fmt_money(amount) -> str:
    try:
        v = round(float(amount), 2)
    except (TypeError, ValueError):
        return f"₹{amount}"
    return f"₹{int(v)}" if v == int(v) else f"₹{v:.2f}"


def fmt_time(ts, fmt="%d %b %Y, %I:%M %p") -> str:
    if not ts:
        return "N/A"
    try:
        dt = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=TZ_OFFSET_MIN)
        return dt.strftime(fmt)
    except ValueError:
        return str(ts)


def uname(u: Dict) -> str:
    if u.get("username"):
        return "@" + esc(u["username"])
    return esc(u.get("first_name") or str(u.get("telegram_id")))


def guess_hours(plan_name: str) -> Optional[int]:
    """Plan duration in hours: '2 Days' -> 48, '1 Month' -> 720, 'Lifetime' -> 0, unknown -> None."""
    name = plan_name.strip()
    if name in API_HOURS_MAP:
        return API_HOURS_MAP[name]
    low = name.lower()
    m = re.search(r"(\d+)\s*(hours?|hrs?|h|days?|d|weeks?|w|months?|mo|years?|y)\b", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("mo"):
            return n * 720
        if unit.startswith("h"):
            return n
        if unit.startswith("d"):
            return n * 24
        if unit.startswith("w"):
            return n * 168
        return n * 8760  # years
    if re.search(r"life\s*-?\s*time|forever|permanent|unlimited", low):
        return 0
    return None


def fmt_duration(hours) -> str:
    """48 -> '2 Days', 5 -> '5 Hours', 0 -> 'Lifetime', None -> ''."""
    if hours is None:
        return ""
    h = int(hours)
    if h == 0:
        return "Lifetime"
    if h < 24 or h % 24:
        return f"{h} Hour{'s' if h != 1 else ''}"
    d = h // 24
    return f"{d} Day{'s' if d != 1 else ''}"


def norm_link(text: str) -> Optional[str]:
    """Accepts https://t.me/..., t.me/..., @username, any http(s) link. Returns a clean URL or None."""
    t = (text or "").strip()
    if re.fullmatch(r"@[A-Za-z0-9_]{4,32}", t):
        return "https://t.me/" + t[1:]
    if re.match(r"(t\.me|telegram\.me)/", t, re.I):
        t = "https://" + t
    if re.fullmatch(r"https?://\S{4,300}", t, re.I):
        return t
    return None


def norm_txn_id(text: str) -> Optional[str]:
    """Accepts a normal 12-digit UPI UTR, or a FamPay-style alphanumeric transaction ID
    (8-40 letters/digits, must contain at least one letter). Returns the clean ID or None."""
    t = re.sub(r"\s+", "", text or "")
    if re.fullmatch(r"\d{12}", t):
        return t
    if re.fullmatch(r"[A-Za-z0-9_\-]{8,40}", t) and re.search(r"[A-Za-z]", t) and re.search(r"\d", t):
        return t
    return None


def utc_now_str(add_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=add_hours)).strftime("%Y-%m-%d %H:%M:%S")


def generate_upi_qr(amount: float) -> io.BytesIO:
    url = f"upi://pay?pa={UPI_ID}&pn={quote(STORE_NAME)}&am={amount:.2f}&cu=INR"
    bio = io.BytesIO()
    qrcode.make(url).save(bio, "PNG")
    bio.seek(0)
    return bio


# ─── media: multiple photos / videos (welcome screen, products, demos) ───
_media_msgs: Dict[int, List[int]] = {}   # chat_id -> album message ids, deleted when the user opens the next screen


async def send_media(bot, chat_id: int, items: List[Dict]) -> None:
    """Sends the saved media in order: photos/videos as albums (up to 10 per album), GIFs and files one by one.
    If an album fails, its files are re-sent one by one so nothing silently disappears."""
    ids: List[int] = []

    async def single(m: Dict):
        t, fid = m["media_type"], m["file_id"]
        if t == "video":
            return await bot.send_video(chat_id, fid)
        if t == "animation":
            return await bot.send_animation(chat_id, fid)
        if t == "document":
            return await bot.send_document(chat_id, fid)
        return await bot.send_photo(chat_id, fid)

    async def send_one(m: Dict):
        try:
            s = await single(m)
            ids.append(s.message_id)
        except Exception as e:
            logger.warning(f"send_media single failed ({m.get('media_type')}): {e}")

    async def flush(chunk: List[Dict]):
        if not chunk:
            return
        if len(chunk) == 1:
            await send_one(chunk[0])
            return
        try:
            group = [InputMediaVideo(m["file_id"]) if m["media_type"] == "video"
                     else InputMediaPhoto(m["file_id"]) for m in chunk]
            sent = await bot.send_media_group(chat_id, group, read_timeout=90, write_timeout=90,
                                              connect_timeout=30, pool_timeout=30)
            ids.extend(s.message_id for s in sent)
        except Exception as e:
            logger.warning(f"album failed ({e}); sending {len(chunk)} files one by one")
            for m in chunk:
                await send_one(m)
                await asyncio.sleep(0.3)

    batch: List[Dict] = []
    for m in items:
        if m["media_type"] in ("photo", "video"):
            batch.append(m)
            if len(batch) == 10:
                await flush(batch)
                batch = []
        else:
            await flush(batch)
            batch = []
            await send_one(m)
    await flush(batch)
    if ids:
        _media_msgs.setdefault(chat_id, []).extend(ids)


async def clear_media(bot, chat_id: int) -> None:
    ids = _media_msgs.pop(chat_id, None)
    if not ids:
        return
    try:
        await bot.delete_messages(chat_id, ids)
        return
    except Exception:
        pass
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


# ─── settings that the admin can change from the panel ───
SETTING_DEFS = {   # key: (button label, kind, config variable)
    "upi_id": ("💳 UPI ID", "upi", "UPI_ID"),
    "store_name": ("🏪 Store Name", "text", "STORE_NAME"),
    "support": ("📞 Support Username", "user", "SUPPORT_USERNAME"),
    "min_recharge": ("⬇️ Min Recharge", "num", "MIN_RECHARGE"),
    "max_recharge": ("⬆️ Max Recharge", "num", "MAX_RECHARGE"),
    "referral_percent": ("🎁 Referral %", "pct", "REFERRAL_PERCENT"),
    "fmpay_key": ("🔑 FMPay API Key", "key", "FMPAY_API_KEY"),
    "webapp_url": ("🌐 Pay Page URL", "url", "WEBAPP_URL"),
    "proof_link": ("🧾 Proof Channel Link", "link", "PROOF_LINK"),
    "proof_chat": ("📡 Proof Channel ID", "chat", "PROOF_CHAT"),
}


def apply_settings():
    """Loads the values changed from the admin panel over the defaults from the code/.env."""
    g = globals()
    for key, (_label, kind, var) in SETTING_DEFS.items():
        val = _DEFAULT_CFG[var]
        raw = db.get_setting(key)
        if raw not in (None, ""):
            try:
                val = float(raw) if kind in ("num", "pct") else raw
            except ValueError:
                pass
        g[var] = val
    g["PAY_MODE"] = "manual" if db.get_setting("pay_mode") == "manual" else "auto"


def validate_setting(kind: str, text: str, key: str):
    """-> (value_to_store, error_message)."""
    t = text.strip()
    if kind == "upi":
        if not re.fullmatch(r"[A-Za-z0-9._\-]{2,64}@[A-Za-z0-9]{2,32}", t):
            return None, "❌ Invalid UPI ID. Example: <code>name@bank</code>"
        return t, None
    if kind == "text":
        if not 1 <= len(t) <= 40:
            return None, "❌ Name must be 1-40 characters."
        return t, None
    if kind == "user":
        u = t.lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", u):
            return None, "❌ Send a valid Telegram username, example <code>@yourname</code>"
        return "@" + u, None
    if kind == "key":
        if not re.fullmatch(r"[A-Za-z0-9_\-]{16,100}", t):
            return None, "❌ Invalid API key. Send only the key, example <code>fmpay_xxxxxxxx...</code>"
        return t, None
    if kind == "url":
        p = urlparse(t)
        if p.scheme != "https" or not p.netloc or " " in t:
            return None, ("❌ Send a valid <b>https</b> address, example <code>https://pay.yourdomain.com</code>\n"
                          "(Telegram only opens Mini Apps on https)")
        return t.rstrip("/"), None
    if kind == "link":
        link = norm_link(t)
        if not link:
            return None, "❌ Send a valid link, example <code>https://t.me/yourchannel</code>"
        return link, None
    if kind == "chat":
        if re.fullmatch(r"-?\d{5,20}", t) or re.fullmatch(r"@[A-Za-z0-9_]{4,32}", t):
            return t, None
        return None, ("❌ Send the channel chat ID (example <code>-1001234567890</code>) "
                      "or the channel username like <code>@yourchannel</code>")
    try:
        v = float(t)
    except ValueError:
        return None, "❌ Send a valid number."
    if kind == "pct":
        if not 0 <= v <= 50:
            return None, "❌ Percent must be between 0 and 50."
    else:
        if v <= 0:
            return None, "❌ Amount must be greater than 0."
        if key == "min_recharge" and v > MAX_RECHARGE:
            return None, f"❌ Min cannot be more than the max ({fmt_money(MAX_RECHARGE)})."
        if key == "max_recharge" and v < MIN_RECHARGE:
            return None, f"❌ Max cannot be less than the min ({fmt_money(MIN_RECHARGE)})."
    return (str(int(v)) if v == int(v) else str(v)), None


def flag(key: str, default: bool = True) -> bool:
    """On/off switches stored in settings ('1' / '0')."""
    v = db.get_setting(key)
    return default if v is None else v == "1"


def proof_active() -> bool:
    """Proof button is shown on the main page only if it is ON and there is something to show."""
    return flag("proof_on") and bool(PROOF_LINK or db.count_media("proof"))


# ─── flood protection / locks ───
_last_action: Dict[int, float] = {}
_locks: Dict[int, asyncio.Lock] = {}


def flooded(uid: int, gap: float = 0.5) -> bool:
    now = time.monotonic()
    if now - _last_action.get(uid, 0) < gap:
        return True
    _last_action[uid] = now
    return False


def user_lock(uid: int) -> asyncio.Lock:
    return _locks.setdefault(uid, asyncio.Lock())


# ─── bold text helpers ───
DIV = "━━━━━━━━━━━━━━━━━━━━"
RAW = "⁣"  # prefix: send this text exactly as given (used for broadcast)

_BOLD_TABLE = {}
for _i, _c in enumerate(string.ascii_uppercase):
    _BOLD_TABLE[ord(_c)] = chr(0x1D5D4 + _i)
for _i, _c in enumerate(string.ascii_lowercase):
    _BOLD_TABLE[ord(_c)] = chr(0x1D5EE + _i)
for _i, _c in enumerate(string.digits):
    _BOLD_TABLE[ord(_c)] = chr(0x1D7EC + _i)


def bold(s: str) -> str:
    """Unicode bold letters - used for button labels (buttons cannot use HTML)."""
    return s.translate(_BOLD_TABLE)


_SKIP_SEGMENTS = re.compile(r"(<code>.*?</code>|<pre>.*?</pre>|<a .*?</a>|<s>.*?</s>)")


def boldify(text: str) -> str:
    """Makes every line of an HTML message bold (code blocks stay as they are)."""
    out = []
    for line in text.split("\n"):
        parts = _SKIP_SEGMENTS.split(line)
        new = []
        for p in parts:
            if p.startswith(("<code>", "<pre>", "<a ", "<s>")):
                new.append(p)
            else:
                q = p.replace("<b>", "").replace("</b>", "")
                new.append(f"<b>{q}</b>" if q.strip() else q)
        out.append("".join(new))
    return "\n".join(out)


class BoldBot(ExtBot):
    """Bot that automatically makes all outgoing messages bold."""

    @staticmethod
    def _fix(text):
        if not isinstance(text, str):
            return text
        if text.startswith(RAW):
            return text[len(RAW):]
        return boldify(text)

    # ─── auto-delete: when a new page (message with buttons) opens, the old page is deleted ───
    _pages: Dict[int, int] = {}   # chat_id -> message_id of the page currently on screen

    async def _new_page(self, chat_id, sent, reply_markup):
        """Remembers `sent` as the current page of this chat and deletes the previous page."""
        try:
            if not reply_markup or not getattr(sent, "message_id", None):
                return
            cid = int(chat_id)
            old = self._pages.get(cid)
            self._pages[cid] = sent.message_id
            if old and old != sent.message_id:
                try:
                    await self.delete_message(cid, old)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"page tracking skipped: {e}")

    def _remember_edit(self, kwargs):
        """A page edited in place (e.g. an old message after a bot restart) becomes the current page."""
        try:
            cid, mid = kwargs.get("chat_id"), kwargs.get("message_id")
            if cid is not None and mid is not None and kwargs.get("reply_markup"):
                self._pages[int(cid)] = int(mid)
        except (TypeError, ValueError):
            pass

    async def send_message(self, chat_id, text, *args, **kwargs):
        keep = kwargs.pop("keep_page", False)   # keep_page=True: do not touch the page tracking (channel posts, broadcasts)
        sent = await super().send_message(chat_id, self._fix(text), *args, **kwargs)
        if not keep:
            await self._new_page(chat_id, sent, kwargs.get("reply_markup"))
        return sent

    async def edit_message_text(self, text, *args, **kwargs):
        res = await super().edit_message_text(self._fix(text), *args, **kwargs)
        self._remember_edit(kwargs)
        return res

    async def send_photo(self, chat_id, photo, caption=None, *args, **kwargs):
        keep = kwargs.pop("keep_page", False)
        sent = await super().send_photo(chat_id, photo, self._fix(caption), *args, **kwargs)
        if not keep:
            await self._new_page(chat_id, sent, kwargs.get("reply_markup"))
        return sent

    async def send_document(self, chat_id, document, caption=None, *args, **kwargs):
        return await super().send_document(chat_id, document, self._fix(caption), *args, **kwargs)

    async def edit_message_caption(self, *args, caption=None, **kwargs):
        res = await super().edit_message_caption(*args, caption=self._fix(caption), **kwargs)
        self._remember_edit(kwargs)
        return res


# ─── colourful bold buttons (Bot API 9.4 `style`) ───
def btn(text: str, cb: Optional[str] = None, style: Optional[str] = "primary", url: Optional[str] = None,
        web_app: Optional[str] = None):
    """Every button is coloured + bold. style: 'primary' blue | 'success' green | 'danger' red.
    Falls back safely (no colour) on old library versions. web_app = opens a Mini App inside Telegram."""
    label = bold(text)
    kw: Dict[str, Any] = {}
    if cb:
        kw["callback_data"] = cb
    if url:
        kw["url"] = url
    if web_app:
        kw["web_app"] = WebAppInfo(url=web_app)
    if style:
        try:
            return InlineKeyboardButton(label, style=style, **kw)
        except TypeError:
            try:
                return InlineKeyboardButton(label, api_kwargs={"style": style}, **kw)
            except TypeError:
                pass
    return InlineKeyboardButton(label, **kw)


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def back_btn(to: str) -> InlineKeyboardMarkup:
    return kb([[btn("⬅️ Back", f"back:{to}", "primary")]])


def cancel_row():
    return [btn("❌ Cancel", "cancel_action", "danger")]


def head(icon: str, title: str) -> str:
    return f"{icon} <b>{title}</b>\n{DIV}\n"


def mask_name(u: Dict) -> str:
    name = u.get("username") or u.get("first_name") or "user"
    return esc(name[:2] + "•••")


def main_menu(user_id: int) -> InlineKeyboardMarkup:
    # every active product (added from the admin panel) is a button right on the main page
    rows = [[btn(p["name"], f"prod:{p['id']}", "primary")] for p in db.get_products()]
    rows += [
        [btn("💰 Wallet", "wallet", "success"), btn("📦 My Orders", "orders", "primary")],
        [btn("👤 Profile", "profile", "primary"), btn("🎁 Redeem Coupon", "redeem_coupon", "success")],
        [btn("📖 Help", "help", "success"), btn("📞 Support", "support", "primary")],
    ]
    if proof_active():
        rows.append([btn("🧾 Payment Proofs", "proof", "success")])
    if is_admin(user_id):
        rows.append([btn("⚙️ Admin Panel", "admin", "danger")])
    return kb(rows)


def welcome_text(user: Dict) -> str:
    custom = db.get_setting("welcome_msg")
    body = custom if custom else (
        "🛒 Your Trusted Gaming Services Store\n\n"
        "⚡ Fast Delivery\n🔒 Safe &amp; Secure\n💎 Premium Services")
    return (
        f"👋 <b>Welcome to {esc(STORE_NAME)}</b>\n{DIV}\n"
        f"{body}\n{DIV}\n"
        f"💰 Balance: <b>{fmt_money(user['balance'])}</b>\n\n"
        f"Please select an option below 👇"
    )


def gate_text(user: Dict, uid: int) -> Optional[str]:
    """Returns a message if this user must be blocked (banned / maintenance)."""
    if is_admin(uid):
        return None
    if user.get("banned"):
        return (head("🚫", "Access Restricted") +
                "Your account has been restricted.\n\n"
                f"If you think this is a mistake, please contact {esc(SUPPORT_USERNAME)}.")
    if db.get_setting("maintenance") == "1":
        now = (datetime.now(timezone.utc) + timedelta(minutes=TZ_OFFSET_MIN)).strftime("%I:%M %p")
        return (head("🔧", "Scheduled Maintenance") +
                f"We are upgrading <b>{esc(STORE_NAME)}</b> to give you a faster and safer experience.\n\n"
                "⏳ Status: <b>Under maintenance</b>\n"
                "🔐 Wallet balance &amp; orders: <b>Safe</b>\n"
                "🔔 Service resumes: <b>Very soon</b>\n\n"
                "No action is needed from your side. Please check again in a few minutes.\n\n"
                f"📞 Support: {esc(SUPPORT_USERNAME)}\n🕐 Last checked: {now}")
    return None


def gate_markup(user: Dict) -> InlineKeyboardMarkup:
    rows = []
    if not user.get("banned"):
        rows.append([btn("🔄 Check Again", "back:main", "success")])
    rows.append([btn("📞 Contact Support", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}", style="primary")])
    return kb(rows)


def help_text() -> str:
    return (
        head("📖", "Help Guide") +
        "🛒 Products - Tap any product on the main page to see plans, demo and buy\n"
        "💰 Wallet - Add balance via UPI\n"
        "📦 Orders - Your purchases, VIP links and keys\n"
        "👤 Profile - Stats and referral link\n"
        "🎁 Coupons - Redeem discount codes\n\n"
        f"{DIV}\n💳 <b>How to add balance?</b>\n"
        f"1. Wallet → Add Balance\n2. Select amount\n3. Pay the exact amount to <code>{esc(UPI_ID)}</code>\n"
        "4. Tap <b>I Paid</b> and send your 12-digit UTR (FamPay: Transaction ID)\n5. Balance is added instantly after verification ✅\n\n"
        f"📞 Support: {esc(SUPPORT_USERNAME)}"
    )


async def edit(query, text: str, markup=None):
    """Edit the message, whether it is text or a photo. Falls back to delete + resend."""
    try:
        if query.message and (query.message.photo or query.message.video or query.message.animation):
            await query.edit_message_caption(caption=text, reply_markup=markup, parse_mode="HTML")
        else:
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        return
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.get_bot().send_message(
        chat_id=query.message.chat_id, text=text, reply_markup=markup, parse_mode="HTML")


async def notify_admins(bot, text: str, markup=None, photo: Optional[str] = None):
    for aid in ADMIN_IDS:
        try:
            if photo:
                await bot.send_photo(aid, photo, caption=text, reply_markup=markup, parse_mode="HTML")
            else:
                await bot.send_message(aid, text, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Could not notify admin {aid}: {e}")


async def show_main(bot, chat_id: int, user: Dict, query=None):
    """Main page: optional welcome photos/videos, then the welcome text with all product buttons."""
    text = welcome_text(user)
    markup = main_menu(user["telegram_id"])
    media = db.get_media("welcome")
    if media:
        if query:
            try:
                await query.message.delete()
            except Exception:
                pass
        await send_media(bot, chat_id, media)
        await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
    elif query:
        await edit(query, text, markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def ensure_user(tg_user, referred_by: Optional[int] = None) -> Dict:
    user = db.get_user(tg_user.id)
    if not user:
        db.create_user(tg_user.id, tg_user.username, tg_user.first_name, referred_by)
        user = db.get_user(tg_user.id)
    elif user.get("username") != tg_user.username:
        db.update_user_info(tg_user.id, tg_user.username, tg_user.first_name)
        user["username"] = tg_user.username
    return user


# ═══════════════════════════════════════════════════════════════════
# API INTEGRATION
# ═══════════════════════════════════════════════════════════════════

KEY_FIELDS = ("key", "license", "license_key", "licenseKey", "token")


def extract_key(d: Any) -> Optional[str]:
    if not isinstance(d, dict):
        return None
    sources = [d]
    for nested in ("data", "result"):
        if isinstance(d.get(nested), dict):
            sources.append(d[nested])
    for src in sources:
        for f in KEY_FIELDS:
            v = src.get(f)
            if isinstance(v, str) and v.strip():
                return v.strip()
    skip = {"message", "msg", "status", "error", "detail"}
    for k, v in d.items():
        if k not in skip and isinstance(v, str) and len(v) > 10:
            return v
    return None


def api_error(d: Dict) -> Optional[str]:
    if d.get("error"):
        return str(d["error"])
    if d.get("success") is False or str(d.get("status", "")).lower() in ("error", "failed", "fail"):
        return str(d.get("message") or d.get("msg") or "API request failed")
    return None


async def generate_key_via_api(hours: int, version: str = "v2", android_id: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"hours": hours}
    if version == "v1":
        payload["version"] = "v1"
        payload["android_id"] = android_id or "unknown"
    try:
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: requests.post(API_URL, headers=API_HEADERS, json=payload, timeout=30))
        try:
            data = resp.json()
        except ValueError:
            return {"error": f"Invalid API response (HTTP {resp.status_code})"}
        if resp.status_code >= 400 and not api_error(data):
            data["error"] = f"HTTP {resp.status_code}"
        logger.info(f"API Response: {data}")
        return data
    except Exception as e:
        logger.error(f"API Error: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        ref_user = db.get_user_by_ref(context.args[0][4:])
        if ref_user and ref_user["telegram_id"] != tg.id:
            referred_by = ref_user["id"]

    user = ensure_user(tg, referred_by)
    blocked = gate_text(user, tg.id)
    if blocked:
        await update.message.reply_text(blocked, reply_markup=gate_markup(user))
        return
    context.user_data.clear()
    await clear_media(context.bot, update.effective_chat.id)
    await show_main(context.bot, update.effective_chat.id, user)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(help_text(), reply_markup=kb([[btn("⬅️ Main Menu", "back:main", "primary")]]))


# ═══════════════════════════════════════════════════════════════════
# CALLBACK ROUTER
# ═══════════════════════════════════════════════════════════════════

ADMIN_PREFIXES = ("admin", "toggle_prod", "del_", "edit_prod",
                  "edit_plan", "export_orders", "prod_group", "ban_toggle", "add_plan")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    data = query.data or ""
    await query.answer()

    if not is_admin(uid) and flooded(uid, 0.4):
        return

    user = ensure_user(update.effective_user)
    blocked = gate_text(user, uid)
    if blocked:
        await edit(query, blocked, gate_markup(user))
        return
    if data.startswith(ADMIN_PREFIXES) and not is_admin(uid):
        return

    await clear_media(context.bot, query.message.chat_id)
    context.user_data.pop("state", None)
    bot_username = (await context.bot.get_me()).username

    # ─── User navigation ───
    if data == "store":
        await show_main(context.bot, query.message.chat_id, user, query)
    elif data.startswith("prod:"):
        await show_product(query, context, int(data.split(":")[1]))
    elif data.startswith("demo:"):
        await show_demo(query, context, int(data.split(":")[1]))
    elif data.startswith("buy:"):
        _, pid, plid = data.split(":")
        await initiate_purchase(query, context, user, int(pid), int(plid))
    elif data.startswith("confirm_buy:"):
        _, pid, plid = data.split(":")
        await process_purchase(query, context, uid, int(pid), int(plid))
    elif data == "wallet":
        await show_wallet(query, user)
    elif data == "wallet_history":
        await show_wallet_history(query, user)
    elif data == "add_balance":
        await ask_amount(query)
    elif data.startswith("amount:"):
        amt = data.split(":")[1]
        if amt == "custom":
            context.user_data["state"] = "await_amount"
            await edit(query, f"💳 <b>Enter amount to add</b>\n\nMinimum {fmt_money(MIN_RECHARGE)}, "
                              f"maximum {fmt_money(MAX_RECHARGE)}:",
                       kb([cancel_row()]))
        else:
            await send_payment_details(context, query.message.chat_id, float(amt), user, query)
    elif data.startswith("utr_verify:"):
        context.user_data["state"] = "await_utr"
        context.user_data["utr_tx_id"] = int(data.split(":")[1])
        await context.bot.send_message(
            query.message.chat_id,
            "🔢 <b>Send your UTR / Transaction ID</b>\n\n"
            "• Any UPI app (GPay, PhonePe, Paytm...): send the <b>12-digit UTR</b>\n"
            "• FamPay: send the <b>Transaction ID</b> shown in the payment details\n\n"
            "You can find it in your app under payment details:",
            reply_markup=kb([cancel_row()]), parse_mode="HTML")
    elif data == "orders":
        await show_orders(query, user)
    elif data == "profile":
        await show_profile(query, user, bot_username)
    elif data == "support":
        await edit(query,
                   head("📞", "Support") +
                   f"Need help? Contact:\n👤 {esc(SUPPORT_USERNAME)}\n\n"
                   f"⏰ Response time: 10-30 minutes\nDescribe your issue clearly for quick help.",
                   kb([[btn("💬 Chat With Support", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}",
                            style="success")],
                       [btn("⬅️ Back", "back:main", "primary")]]))
    elif data == "help":
        await edit(query, help_text(), kb([[btn("⬅️ Back", "back:main", "primary")]]))
    elif data == "proof":
        await show_proof(query, context)
    elif data == "redeem_coupon":
        context.user_data["state"] = "await_coupon"
        await edit(query, "🎁 <b>Enter coupon code:</b>", kb([cancel_row()]))
    elif data == "referral":
        await show_referral(query, user, bot_username)
    elif data.startswith("back:"):
        dest = data.split(":")[1]
        if dest in ("main", "store"):
            await show_main(context.bot, query.message.chat_id, user, query)
        elif dest == "wallet":
            await show_wallet(query, user)
        elif dest == "orders":
            await show_orders(query, user)
        elif dest == "profile":
            await show_profile(query, user, bot_username)
        elif dest == "admin":
            await show_admin(query)
        elif dest == "admin_products":
            await show_admin_products(query)
    elif data == "cancel_action":
        context.user_data.clear()
        await edit(query, "❌ <b>Action cancelled.</b>", main_menu(uid))

    # ─── Admin ───
    elif data == "admin":
        await show_admin(query)
    elif data == "admin_dashboard":
        await show_admin_dashboard(query)
    elif data == "admin_users":
        await show_admin_users(query)
    elif data == "admin_orders":
        await show_admin_orders(query)
    elif data == "admin_report":
        await show_admin_report(query)
    elif data == "export_orders":
        await export_orders(query, context)
    elif data == "admin_search":
        context.user_data["state"] = "await_search"
        await edit(query, "🔎 <b>Find User</b>\n\nSend Telegram ID or @username:", kb([cancel_row()]))
    elif data.startswith("ban_toggle:"):
        await toggle_ban(query, context, int(data.split(":")[1]))
    elif data == "admin_products":
        await show_admin_products(query)
    elif data == "admin_add_product":
        context.user_data["state"] = "await_prod_name"
        await edit(query, "➕ <b>Add New Product</b>\n\n<b>Step 1/5:</b> Send the product name / title.\n"
                          "You can use many emojis, e.g. <code>👑🔥 VIP PREMIUM 🔥👑</code>", kb([cancel_row()]))
    elif data.startswith("toggle_prod:"):
        pid = int(data.split(":")[1])
        p = db.get_product(pid)
        if p:
            new = "inactive" if p["status"] == "active" else "active"
            db.set_product_status(pid, new)
            db.log_admin(uid, f"product {pid} -> {new}")
        await show_admin_products(query)
    elif data.startswith("edit_prod:"):
        await show_edit_product(query, int(data.split(":")[1]))
    elif data.startswith("edit_plan:"):
        await show_plan_menu(query, int(data.split(":")[1]))
    elif data.startswith("del_prod:"):
        pid = int(data.split(":")[1])
        p = db.get_product(pid)
        name = esc(p["name"]) if p else "this product"
        await edit(query, f"🗑️ Delete <b>{name}</b>?\nThis cannot be undone.",
                   kb([[btn("🗑️ Yes, delete", f"del_ok:{pid}", "danger")],
                       [btn("⬅️ No, go back", "admin_products")]]))
    elif data.startswith("del_ok:"):
        pid = int(data.split(":")[1])
        db.delete_product(pid)
        db.log_admin(uid, f"deleted product {pid}")
        await show_admin_products(query)
    elif data == "admin_coupons":
        await show_admin_coupons(query)
    elif data == "admin_add_coupon":
        context.user_data["state"] = "await_coupon_code"
        await edit(query,
                   "🎁 <b>Add Coupon</b>\n\nFormat: <code>CODE TYPE VALUE DAYS LIMIT</code>\n"
                   "Types: <code>BALANCE</code> (adds wallet money), <code>PERCENT</code> / <code>FLAT</code> (discount on next purchase)\n"
                   "DAYS = 0 for no expiry. LIMIT = total uses.\n\n"
                   "<code>SAVE10 PERCENT 10 30 100</code>\n<code>WELCOME BALANCE 50 7 20</code>",
                   kb([cancel_row()]))
    elif data == "admin_broadcast":
        context.user_data["state"] = "await_broadcast"
        context.user_data.pop("bc", None)
        await edit(query,
                   head("📢", "Broadcast") + "<b>Step 1/2:</b> Send the message for all users.\n\n"
                   "• Text only, or\n• One photo / video with the text as its caption\n\n"
                   "Bold / italic / links formatting works.",
                   kb([cancel_row()]))
    elif data == "admin_bc_send":
        await start_broadcast(context, query)
    elif data == "admin_add_balance":
        context.user_data["state"] = "await_admin_user_id"
        context.user_data["bal_mode"] = "add"
        await edit(query, head("➕", "Add Balance") + "Send user Telegram ID:", kb([cancel_row()]))
    elif data == "admin_deduct":
        context.user_data["state"] = "await_admin_user_id"
        context.user_data["bal_mode"] = "deduct"
        await edit(query, head("➖", "Deduct Balance") + "Send user Telegram ID:", kb([cancel_row()]))
    elif data == "admin_maint":
        new = "0" if db.get_setting("maintenance") == "1" else "1"
        db.set_setting("maintenance", new)
        db.log_admin(uid, f"maintenance -> {new}")
        await show_admin(query)
    elif data == "admin_top":
        await show_admin_top(query)
    elif data == "admin_banned":
        await show_admin_banned(query)
    elif data == "admin_logs":
        await show_admin_logs(query)
    elif data == "admin_backup":
        await send_backup(query, context)
    elif data == "admin_welcome":
        context.user_data["state"] = "await_welcome"
        await edit(query,
                   head("📝", "Welcome Message") +
                   "Send the new welcome text (bold/italic formatting works).\n"
                   "Send <code>-</code> to reset to the default message.",
                   kb([cancel_row()]))
    elif data.startswith("add_plan:"):
        context.user_data["state"] = "await_new_plan"
        context.user_data["plan_product_id"] = int(data.split(":")[1])
        await edit(query,
                   head("➕", "Add Plan") +
                   "Send plan in this format:\n<code>Name = Price</code>\n\nExample: <code>2 Days = 500</code>\n"
                   "Plan name must contain hours or days.",
                   kb([cancel_row()]))
    elif data.startswith("del_plan:"):
        plan = db.get_plan(int(data.split(":")[1]))
        if plan:
            pid = plan["product_id"]
            db.delete_plan(plan["id"])
            db.log_admin(uid, f"deleted plan {plan['id']} ({plan['plan_name']})")
            await show_edit_product(query, pid)
    elif data.startswith("del_coupon:"):
        db.delete_coupon(int(data.split(":")[1]))
        db.log_admin(uid, f"deleted coupon {data.split(':')[1]}")
        await show_admin_coupons(query)

    # ─── Admin: proof channel / reminder ───
    elif data == "admin_proof":
        await show_admin_proof(query)
    elif data.startswith("admin_flag:"):
        key = data.split(":", 1)[1]
        if key in ("proof_on", "proof_post", "remind_on"):
            new = "0" if flag(key) else "1"
            db.set_setting(key, new)
            db.log_admin(uid, f"{key} -> {new}")
        await show_admin_proof(query)
    elif data == "admin_proof_test":
        await admin_proof_test(query, context)

    # ─── Admin: settings (UPI ID, store name, support, limits) ───
    elif data == "admin_settings":
        await show_admin_settings(query)
    elif data.startswith("admin_paymode:"):
        new_mode = "manual" if PAY_MODE == "auto" else "auto"
        db.set_setting("pay_mode", new_mode)
        apply_settings()
        db.log_admin(uid, f"payment mode -> {new_mode}")
        if data.endswith(":main"):
            await show_admin(query)
        else:
            await show_admin_settings(query)
    elif data.startswith("admin_man_ok:") or data.startswith("admin_man_no:"):
        await manual_decision(context, query, uid, data)
    elif data.startswith("admin_set:"):
        key = data.split(":", 1)[1]
        if key in SETTING_DEFS:
            context.user_data["state"] = "await_setting"
            context.user_data["setting_key"] = key
            await edit(query, setting_prompt(key), kb([cancel_row()]))

    # ─── Admin: media (welcome screen / product / demo) ───
    elif data.startswith("admin_mm:"):
        await show_media_manager(query, data.split(":", 1)[1])
    elif data.startswith("admin_madd:"):
        owner = data.split(":", 1)[1]
        if db.count_media(owner) >= MAX_MEDIA_PER_ITEM:
            await edit(query, f"❌ Maximum {MAX_MEDIA_PER_ITEM} files reached. Clear some first.",
                       kb([[btn("⬅️ Back", f"admin_mm:{owner}", "primary")]]))
        else:
            context.user_data["state"] = "await_media"
            context.user_data["media_owner"] = owner
            await edit(query,
                       head("🖼", "Add Photos / Videos") +
                       "Send photos or videos now - one by one or as an album.\n"
                       "Send them as normal photo/video (not as a file).\n\nTap <b>Done</b> when finished.",
                       kb([[btn("✅ Done", f"admin_mm:{owner}", "success")]]))
    elif data.startswith("admin_mview:"):
        await preview_media(query, context, data.split(":", 1)[1])
    elif data.startswith("admin_mclear:"):
        owner = data.split(":", 1)[1]
        db.clear_media(owner)
        db.log_admin(uid, f"cleared media {owner}")
        await show_media_manager(query, owner)

    # ─── Admin: product fields / delivery / plans ───
    elif data.startswith("admin_pf:"):
        _, pid_s, field = data.split(":")
        p = db.get_product(int(pid_s))
        if p and field in PROD_FIELDS:
            context.user_data["state"] = "await_prod_field"
            context.user_data["prod_edit_id"] = p["id"]
            context.user_data["prod_edit_field"] = field
            await edit(query, prod_field_prompt(p, field), kb([cancel_row()]))
    elif data.startswith("admin_api:"):
        pid = int(data.split(":")[1])
        p = db.get_product(pid)
        if p:
            db.set_product_api(pid, 0 if p["use_api"] else 1)
            db.log_admin(uid, f"product {pid} delivery -> {'vip link' if p['use_api'] else 'api key'}")
            await show_edit_product(query, pid)
    elif data.startswith("admin_plan_price:"):
        plan = db.get_plan(int(data.split(":")[1]))
        if plan:
            context.user_data["state"] = "await_plan_price"
            context.user_data["edit_plan_id"] = plan["id"]
            await edit(query, f"💰 <b>{esc(plan['plan_name'])}</b>\nCurrent price: {fmt_money(plan['price'])}\n\n"
                              f"Send the new price:", kb([cancel_row()]))
    elif data.startswith("admin_plan_name:"):
        plan = db.get_plan(int(data.split(":")[1]))
        if plan:
            context.user_data["state"] = "await_plan_name"
            context.user_data["plan_edit_id"] = plan["id"]
            await edit(query,
                       f"⏱ <b>{esc(plan['plan_name'])}</b>\n\nSend the new plan name with its duration, e.g.\n"
                       f"<code>7 Days</code>  <code>30 Days</code>  <code>1 Month</code>  <code>Lifetime</code>\n\n"
                       f"The duration in the name decides how long the VIP stays.", kb([cancel_row()]))


# ═══════════════════════════════════════════════════════════════════
# STORE
# ═══════════════════════════════════════════════════════════════════

async def show_product(query, context, product_id: int):
    product = db.get_product(product_id)
    if not product or product["status"] != "active":
        await edit(query, "❌ Product not available.", back_btn("main"))
        return
    plans = db.get_product_plans(product_id)
    if not plans:
        await edit(query, "No plans available.", back_btn("main"))
        return
    text = f"<b>{esc(product['name'])}</b>\n\n"
    if product.get("description"):
        text += f"{esc(product['description'])}\n\n"
    text += "<b>Available Plans:</b>"
    rows = [[btn(f"🛒 {p['plan_name']} - {fmt_money(p['price'])}", f"buy:{product_id}:{p['id']}", "success")]
            for p in plans]
    if product.get("demo_link"):
        rows.append([btn(product.get("demo_title") or "🎬 Demo Channel", url=product["demo_link"], style="primary")])
    if db.count_media(f"p{product_id}:demo"):
        rows.append([btn("📸 Demo Photos / Videos", f"demo:{product_id}", "primary")])
    rows.append([btn("⬅️ Back", "back:main")])

    media = db.get_media(f"p{product_id}:main")
    if media:
        chat_id = query.message.chat_id
        try:
            await query.message.delete()
        except Exception:
            pass
        await send_media(context.bot, chat_id, media)
        await context.bot.send_message(chat_id, text, reply_markup=kb(rows), parse_mode="HTML")
    else:
        await edit(query, text, kb(rows))


async def show_demo(query, context, product_id: int):
    product = db.get_product(product_id)
    media = db.get_media(f"p{product_id}:demo")
    if not product or product["status"] != "active" or not media:
        await edit(query, "❌ No demo available.", back_btn("main"))
        return
    rows = []
    if product.get("demo_link"):
        rows.append([btn(product.get("demo_title") or "🎬 Demo Channel", url=product["demo_link"], style="primary")])
    rows.append([btn("⬅️ Back", f"prod:{product_id}", "primary")])
    chat_id = query.message.chat_id
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_media(context.bot, chat_id, media)
    await context.bot.send_message(chat_id, f"🎬 <b>Demo - {esc(product['name'])}</b>",
                                   reply_markup=kb(rows), parse_mode="HTML")


def price_with_coupon(user: Dict, price: float):
    """Returns (final_price, discount, coupon_code)."""
    code = user.get("active_coupon")
    if not code:
        return price, 0.0, None
    c = db.get_coupon(code)
    if not c:
        return price, 0.0, None
    if c["type"] == "PERCENT":
        discount = price * float(c["value"]) / 100
    elif c["type"] == "FLAT":
        discount = float(c["value"])
    else:
        return price, 0.0, None
    final = max(round(price - discount, 2), 1.0)
    return final, round(price - final, 2), code


def order_summary(product, plan, price, discount, code, balance, android_id=None) -> str:
    text = (f"📦 <b>ORDER SUMMARY</b>\n\n🎮 Product: <b>{esc(product['name'])}</b>\n"
            f"📋 Plan: <b>{esc(plan['plan_name'])}</b>\n")
    dur = fmt_duration(plan.get("api_hours"))
    if dur:
        text += f"⏳ Validity: <b>{dur}</b>\n"
    if android_id:
        text += f"📱 Android ID: <code>{esc(android_id)}</code>\n"
    if discount > 0:
        text += (f"🏷️ Coupon <b>{esc(code)}</b>: -{fmt_money(discount)}\n"
                 f"💵 Original: <s>{fmt_money(plan['price'])}</s>\n")
    text += (f"💰 Price: <b>{fmt_money(price)}</b>\n"
             f"💳 Balance After: <b>{fmt_money(balance - price)}</b>\n\nDo you want to confirm?")
    return text


async def initiate_purchase(query, context, user, product_id: int, plan_id: int):
    product = db.get_product(product_id)
    plan = db.get_plan(plan_id)
    if not product or not plan or plan["product_id"] != product_id or product["status"] != "active":
        await edit(query, "❌ Invalid product or plan.", back_btn("store"))
        return

    price, discount, code = price_with_coupon(user, float(plan["price"]))
    balance = float(user["balance"])

    if balance < price - 0.001:
        await edit(query,
                   f"❌ <b>Insufficient Balance!</b>\n\n💰 Required: <b>{fmt_money(price)}</b>\n"
                   f"💳 Your Balance: <b>{fmt_money(balance)}</b>\n\nPlease add balance first.",
                   kb([[btn("💳 Add Balance", "add_balance", "success")],
                       [btn("⬅️ Back", f"prod:{product_id}")]]))
        return

    if product["use_api"] and product["api_version"] == "v1":
        context.user_data["pending_purchase"] = {"product_id": product_id, "plan_id": plan_id}
        context.user_data["state"] = "await_android_id"
        await edit(query,
                   f"📱 <b>{esc(product['name'])}</b> - {esc(plan['plan_name'])}\n\n"
                   f"This product needs your Android ID.\nPlease send it now:",
                   kb([cancel_row()]))
        return

    await edit(query, order_summary(product, plan, price, discount, code, balance),
               kb([[btn("✅ Confirm Order", f"confirm_buy:{product_id}:{plan_id}", "success")],
                   [btn("❌ Cancel", f"prod:{product_id}", "danger")]]))


async def process_purchase(query, context, tg_id: int, product_id: int, plan_id: int):
    lock = user_lock(tg_id)
    if lock.locked():
        return  # double tap: first request is still running
    async with lock:
        user = db.get_user(tg_id)
        product = db.get_product(product_id)
        plan = db.get_plan(plan_id)
        android_id = context.user_data.pop("android_id", None)

        if not user or user["banned"] and not is_admin(tg_id):
            return
        if not product or not plan or plan["product_id"] != product_id or product["status"] != "active":
            await edit(query, "❌ Product/Plan not available.", back_btn("main"))
            return

        use_api = bool(product["use_api"])
        hours = plan.get("api_hours")
        if hours is None:
            hours = guess_hours(plan["plan_name"])
        if use_api and not hours:
            await edit(query, f"❌ This plan is not configured correctly. Contact {esc(SUPPORT_USERNAME)}.",
                       back_btn("main"))
            await notify_admins(context.bot, f"⚠️ Plan <b>{esc(plan['plan_name'])}</b> of "
                                              f"<b>{esc(product['name'])}</b> has no valid duration (hours).")
            return
        if not use_api and not product.get("vip_link"):
            await edit(query, f"❌ This product is not ready yet. Contact {esc(SUPPORT_USERNAME)}.",
                       back_btn("main"))
            await notify_admins(context.bot, f"⚠️ Product <b>{esc(product['name'])}</b> has no VIP link set - "
                                              f"a user tried to buy it. Set it in Admin → Products.")
            return
        if use_api and product["api_version"] == "v1" and not android_id:
            await edit(query, "❌ Android ID missing. Please start the purchase again.", back_btn("main"))
            return

        price, discount, code = price_with_coupon(user, float(plan["price"]))

        if not db.deduct_balance(tg_id, price):
            await edit(query, "❌ <b>Insufficient balance.</b>",
                       kb([[btn("💳 Add Balance", "add_balance", "success")], [btn("⬅️ Back", "back:main")]]))
            return

        await edit(query, "⏳ <b>Processing your order...</b> Please wait.")

        # ── key products: get the key from the API; refund only if THIS part fails ──
        key_code = None
        api_data: Dict[str, Any] = {}
        if use_api:
            error = None
            try:
                api_data = await generate_key_via_api(hours, product.get("api_version", "v2"), android_id)
                error = api_error(api_data)
                if not error:
                    key_code = extract_key(api_data)
                    if not key_code:
                        error = "No key received from API"
            except Exception as e:
                error = str(e)

            if error:
                logger.error(f"Purchase failed for {tg_id}: {error}")
                db.update_balance(tg_id, price)
                await edit(query,
                           f"❌ <b>Order Failed</b>\n\nSomething went wrong while creating your key.\n"
                           f"💰 Your balance has been <b>refunded</b>.\n\nPlease try again later or contact "
                           f"{esc(SUPPORT_USERNAME)}",
                           back_btn("main"))
                await notify_admins(context.bot,
                                    f"⚠️ <b>ORDER FAILED</b> (refunded)\n\nUser: {uname(user)} (<code>{tg_id}</code>)\n"
                                    f"Product: {esc(product['name'])} / {esc(plan['plan_name'])}\n"
                                    f"Error: <code>{esc(error)}</code>")
                return

        # ── delivered: from here we NEVER refund ──
        order_id = generate_order_id()
        vip_link = product.get("vip_link")
        expires_at = utc_now_str(hours) if hours else None
        try:
            db.create_order(order_id, user["id"], product_id, plan_id, plan["plan_name"], price,
                            key_code, android_id, json.dumps(api_data) if api_data else None,
                            vip_link, expires_at)
            db.increment_orders(tg_id)
            if code:
                db.set_active_coupon(tg_id, None)
        except Exception as e:
            logger.error(f"Order save failed (order was delivered): {e}")

        try:
            if user["referred_by"]:
                ref = db.get_user_by_id(user["referred_by"])
                if ref and REFERRAL_PERCENT > 0:
                    commission = round(price * REFERRAL_PERCENT / 100, 2)
                    db.credit_referral(ref["telegram_id"], commission)
                    try:
                        await context.bot.send_message(
                            ref["telegram_id"],
                            f"🎁 <b>Referral bonus!</b> +{fmt_money(commission)} added to your wallet.",
                            parse_mode="HTML")
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Referral error: {e}")

        text = (f"✅ <b>ORDER COMPLETED</b>\n\n🎮 Product: <b>{esc(product['name'])}</b>\n"
                f"📋 Plan: <b>{esc(plan['plan_name'])}</b>\n💰 Paid: <b>{fmt_money(price)}</b>\n"
                f"🆔 Order ID: <code>#{order_id}</code>\n")
        if expires_at:
            text += f"⏳ Valid till: <b>{fmt_time(expires_at)}</b>\n"
        elif hours == 0:
            text += "⏳ Validity: <b>Lifetime</b>\n"
        if key_code:
            text += f"\n🔑 <b>YOUR KEY:</b>\n<code>{esc(key_code)}</code>\n"
        rows = []
        if vip_link:
            title = product.get("vip_title") or "👑 Join VIP Channel"
            text += f"\n👑 <b>{esc(title)}</b>\n{esc(vip_link)}\n"
            rows.append([btn(title, url=vip_link, style="success")])
        text += f"\n📞 Support: {esc(SUPPORT_USERNAME)}\n🚀 Thank you for purchasing from <b>{esc(STORE_NAME)}</b>!"
        rows.append([btn("🏠 Main Menu", "back:main", "primary")])
        try:
            await edit(query, text, kb(rows))
        except Exception:
            await context.bot.send_message(tg_id, text, reply_markup=kb(rows), parse_mode="HTML")

        await notify_admins(context.bot,
                            f"💰 <b>NEW SALE!</b>\n\n🎮 {esc(product['name'])} - {esc(plan['plan_name'])}\n"
                            f"💰 {fmt_money(price)}\n👤 {uname(user)}\n🆔 <code>{tg_id}</code>")
        try:
            await auto_post_proof(context.bot, product, plan, price)
        except Exception as e:
            logger.error(f"auto proof error: {e}")


# ═══════════════════════════════════════════════════════════════════
# WALLET
# ═══════════════════════════════════════════════════════════════════

async def show_wallet(query, user):
    await edit(query,
               f"💰 <b>Wallet</b>\n\nCurrent Balance: <b>{fmt_money(user['balance'])}</b>\n\nSelect an option:",
               kb([[btn("💳 Add Balance", "add_balance", "success")],
                   [btn("📜 Transaction History", "wallet_history")],
                   [btn("🎁 Redeem Coupon", "redeem_coupon")],
                   [btn("⬅️ Back", "back:main")]]))


async def show_wallet_history(query, user):
    txs = db.get_user_transactions(user["id"], 10)
    if not txs:
        await edit(query, "📜 No transactions yet.", back_btn("wallet"))
        return
    icons = {"approved": "✅", "pending": "⏳", "review": "🕒", "rejected": "❌", "expired": "⌛", "duplicate": "⚠️"}
    text = "📜 <b>Last Transactions</b>\n\n"
    for t in txs:
        text += (f"{icons.get(t['status'], '•')} <b>{fmt_money(t['amount'])}</b> - {esc(t['status'].title())}\n"
                 f"   🕐 {fmt_time(t['created_at'])}\n")
        if t.get("utr"):
            text += f"   🔢 <code>{esc(t['utr'])}</code>\n"
        text += "\n"
    await edit(query, text, back_btn("wallet"))


async def ask_amount(query):
    await edit(query, f"💳 <b>Add Balance</b>\n\nSelect amount (minimum {fmt_money(MIN_RECHARGE)}):",
               kb([[btn("₹50", "amount:50", "success"), btn("₹100", "amount:100", "success"),
                    btn("₹200", "amount:200", "success")],
                   [btn("₹500", "amount:500", "success"), btn("₹1000", "amount:1000", "success"),
                    btn("✍️ Custom (₹1+)", "amount:custom", "primary")],
                   [btn("⬅️ Back", "back:wallet")]]))


async def send_payment_details(context, chat_id: int, amount: float, user: Dict, query=None):
    """Shows UPI QR. After paying, the user sends the UTR and the bot verifies it."""
    if db.count_open_payments(user["id"], 30) >= 5:
        text = "⚠️ You have too many unfinished payments. Complete one or wait a few minutes."
        if query:
            await edit(query, text, back_btn("wallet"))
        else:
            await context.bot.send_message(chat_id, text, reply_markup=main_menu(user["telegram_id"]))
        return
    ref = "TX" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    tx_id = db.create_payment_tx(user["id"], amount, ref, amount, chat_id)
    after = ("⚡ Balance is added instantly after verification." if PAY_MODE == "auto"
             else "🕒 Balance is added after the admin approves your payment.")
    page = pay_page_url(tx_id)
    if query:
        try:
            await query.message.delete()
        except Exception:
            pass
    try:
        if page:   # Mini App: payment page opens inside Telegram
            text = (f"💳 <b>Payment Details</b>\n\nAmount: <b>{fmt_money(amount)}</b>\n🧾 Ref: <code>{ref}</code>\n\n"
                    f"Tap <b>Pay Now</b> to open the secure payment page (QR + UPI apps).\n"
                    f"After paying, enter your UTR there. FamPay users: use the Transaction ID.\n\n{after}")
            markup = kb([[btn("💳 Pay Now", style="success", web_app=page)],
                         [btn("✅ I Paid - Enter UTR", f"utr_verify:{tx_id}", "primary")], cancel_row()])
            sent = await context.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
        else:      # classic QR photo
            caption = (f"💳 <b>Payment Details</b>\n\nAmount: <b>{fmt_money(amount)}</b>\n"
                       f"UPI ID: <code>{esc(UPI_ID)}</code>\n🧾 Ref: <code>{ref}</code>\n\n✅ <b>Steps:</b>\n"
                       f"1. Scan the QR or pay to the UPI ID\n"
                       f"2. Pay the <b>exact amount</b> shown\n"
                       f"3. Tap <b>I Paid</b> and send the 12-digit UTR\n"
                       f"    (FamPay users: send the Transaction ID)\n\n{after}")
            markup = kb([[btn("✅ I Paid - Enter UTR", f"utr_verify:{tx_id}", "success")], cancel_row()])
            sent = await context.bot.send_photo(
                chat_id, InputFile(generate_upi_qr(amount), filename="payment_qr.png"),
                caption=caption, reply_markup=markup, parse_mode="HTML")
        db.set_tx_message(tx_id, sent.message_id)
    except Exception as e:
        logger.error(f"Could not send payment screen: {e}")


# ═══════════════════════════════════════════════════════════════════
# PROFILE / ORDERS
# ═══════════════════════════════════════════════════════════════════

async def show_profile(query, user, bot_username: str):
    text = (f"👤 <b>Profile</b>\n\n🆔 User ID: <code>{user['telegram_id']}</code>\n"
            f"👤 Username: {uname(user)}\n💰 Balance: <b>{fmt_money(user['balance'])}</b>\n"
            f"📦 Total Orders: <b>{user['total_orders']}</b>\n"
            f"🎁 Referral Earnings: <b>{fmt_money(user['referral_earnings'])}</b>\n"
            f"📅 Joined: {fmt_time(user['created_at'], '%d %b %Y')}\n")
    if user.get("active_coupon"):
        text += f"🏷️ Active coupon: <b>{esc(user['active_coupon'])}</b> (applies to next purchase)\n"
    text += f"\n🔗 Referral Code: <code>{esc(user['referral_code'])}</code>"
    await edit(query, text,
               kb([[btn("➕ Add Balance", "add_balance", "success")],
                   [btn("📦 Order History", "orders"), btn("🔗 Referral", "referral")],
                   [btn("🎁 Redeem Coupon", "redeem_coupon")],
                   [btn("⬅️ Back", "back:main")]]))


async def show_orders(query, user):
    orders = db.get_user_orders(user["id"], 10)
    if not orders:
        await edit(query, "📦 You have no orders yet.",
                   kb([[btn("🛒 Products", "store", "primary")], [btn("⬅️ Back", "back:main")]]))
        return
    text = "📦 <b>My Orders</b> (last 10)\n\n"
    for o in orders:
        if len(text) > 3300:   # keep the message under Telegram's 4096 limit
            break
        text += (f"🆔 <code>#{o['order_id']}</code> | <b>{esc(o['product_name'])}</b>\n"
                 f"   📋 {esc(o['plan_name'])} | {fmt_money(o['price'])}\n"
                 f"   📅 {fmt_time(o['created_at'])}\n")
        if o.get("key_code"):
            text += f"   🔑 <code>{esc(o['key_code'])}</code>\n"
        if o.get("vip_link"):
            text += f"   👑 {esc(o['vip_link'])}\n"
        if o.get("expires_at"):
            gone = str(o["expires_at"]) < utc_now_str()
            text += f"   {'⌛ Expired' if gone else '⏳ Valid till'}: {fmt_time(o['expires_at'])}\n"
        text += "\n"
    await edit(query, text, back_btn("profile"))


async def show_referral(query, user, bot_username: str):
    await edit(query,
               f"🎁 <b>Referral Program</b>\n\nInvite friends and earn <b>{REFERRAL_PERCENT:g}% commission</b> "
               f"in your wallet on every purchase they make!\n\n🔗 Your Link:\n"
               f"<code>https://t.me/{bot_username}?start=ref_{user['referral_code']}</code>\n\n"
               f"💰 Total Earnings: <b>{fmt_money(user['referral_earnings'])}</b>",
               back_btn("profile"))


# ═══════════════════════════════════════════════════════════════════
# ADMIN PANEL
# ═══════════════════════════════════════════════════════════════════

async def show_admin(query):
    pending = len(db.get_pending_transactions())
    maint = db.get_setting("maintenance") == "1"
    maint_label = "🔧 Maintenance: ON" if maint else "🔧 Maintenance: OFF"
    await edit(query,
               head("⚙️", "Admin Panel") +
               f"Pending payments: <b>{pending}</b>\nMaintenance: <b>{'ON 🔴' if maint else 'OFF 🟢'}</b>\n"
               f"Payment mode: <b>{paymode_label()}</b>",
               kb([[btn(f"💳 Payment: {'AUTO 🤖' if PAY_MODE == 'auto' else 'MANUAL ✋'} (tap to switch)",
                        "admin_paymode:main", "success" if PAY_MODE == "auto" else "danger")],
                   [btn("📊 Dashboard", "admin_dashboard", "primary"), btn("📈 Sales Report", "admin_report", "primary")],
                   [btn("👥 Users", "admin_users", "primary"), btn("📦 Orders", "admin_orders", "primary")],
                   [btn("🛍️ Products", "admin_products", "primary"), btn("🎁 Coupons", "admin_coupons", "primary")],
                   [btn("🏆 Top Stats", "admin_top", "primary"), btn("🔎 Find / Ban User", "admin_search", "danger")],
                   [btn("➕ Add Balance", "admin_add_balance", "success"), btn("➖ Deduct Balance", "admin_deduct", "danger")],
                   [btn("📢 Broadcast", "admin_broadcast", "primary"), btn("📝 Welcome Text", "admin_welcome", "primary")],
                   [btn("🖼 Welcome Photos/Videos", "admin_mm:welcome", "primary"),
                    btn("⚙️ Settings / UPI", "admin_settings", "success")],
                   [btn("🧾 Proof / Reminder", "admin_proof", "success")],
                   [btn("🚫 Banned Users", "admin_banned", "danger"), btn("📜 Admin Logs", "admin_logs", "primary")],
                   [btn("💾 Backup DB", "admin_backup", "success"),
                    btn(maint_label, "admin_maint", "danger" if maint else "success")],
                   [btn("⬅️ Back", "back:main", "primary")]]))


async def show_admin_dashboard(query):
    t_cnt, t_sum = db.sales_today()
    pending = db.get_pending_transactions()
    await edit(query,
               head("📊", "Dashboard") +
               f"👥 Total Users: <b>{db.count_users()}</b> (+{db.new_users_today()} today)\n"
               f"📦 Total Orders: <b>{db.count_orders()}</b>\n"
               f"📈 Today: <b>{fmt_money(t_sum)}</b> ({t_cnt} orders)\n"
               f"💳 Pending Payments: <b>{len(pending)}</b> ({fmt_money(db.get_total_pending_amount())})\n"
               f"👛 Users' Wallet Total: <b>{fmt_money(db.total_wallet_balance())}</b>",
               back_btn("admin"))


async def show_admin_top(query):
    text = head("🏆", "Top Stats")
    text += "\n💎 <b>Top Buyers</b>\n"
    buyers = db.get_top_buyers(5)
    for i, b in enumerate(buyers, 1):
        text += f"{i}. {uname(b)} (<code>{b['telegram_id']}</code>) - {fmt_money(b['spent'])} / {b['cnt']} orders\n"
    if not buyers:
        text += "No purchases yet.\n"
    text += f"\n{DIV}\n🔥 <b>Best Selling Products</b>\n"
    prods = db.get_top_products(5)
    for i, p in enumerate(prods, 1):
        text += f"{i}. {esc(p['name'])} - {p['cnt']} sold / {fmt_money(p['total'])}\n"
    if not prods:
        text += "No sales yet.\n"
    refs = db.get_top_referrers(5)
    text += f"\n{DIV}\n🎁 <b>Top Referrers</b>\n"
    for i, r in enumerate(refs, 1):
        text += f"{i}. {uname(r)} - {r['refs']} invites / {fmt_money(r['referral_earnings'])}\n"
    if not refs:
        text += "No referrals yet.\n"
    await edit(query, text, back_btn("admin"))


async def show_admin_banned(query):
    users = db.get_banned_users()
    text = head("🚫", "Banned Users")
    if not users:
        text += "No banned users."
        await edit(query, text, back_btn("admin"))
        return
    rows = []
    for u in users:
        text += f"🆔 <code>{u['telegram_id']}</code> | {uname(u)}\n"
        rows.append([btn(f"✅ Unban {u['telegram_id']}", f"ban_toggle:{u['telegram_id']}", "success")])
    rows.append([btn("⬅️ Back", "back:admin", "primary")])
    await edit(query, text, kb(rows))


async def show_admin_logs(query):
    logs = db.get_logs(15)
    text = head("📜", "Admin Logs")
    if not logs:
        text += "No actions logged yet."
    for l in logs:
        text += f"🕐 {fmt_time(l['created_at'], '%d %b %H:%M')} | <code>{l['admin_id']}</code>\n   {esc(l['action'])}\n"
    await edit(query, text, back_btn("admin"))


async def send_backup(query, context):
    path = os.path.join(BASE_DIR, f"backup_{datetime.now():%Y%m%d_%H%M%S}.db")
    try:
        db.backup_to(path)
        with open(path, "rb") as f:
            await context.bot.send_document(
                query.message.chat_id, InputFile(f, filename=os.path.basename(path)),
                caption="💾 Database backup")
        db.log_admin(query.from_user.id, "downloaded DB backup")
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        await context.bot.send_message(query.message.chat_id, "❌ Backup failed. Check the server logs.")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def show_admin_report(query):
    t_cnt, t_sum = db.sales_today()
    w_cnt, w_sum = db.sales_last_days(7)
    m_cnt, m_sum = db.sales_last_days(30)
    await edit(query,
               f"📈 <b>Sales Report</b>\n\n📅 Today: <b>{fmt_money(t_sum)}</b> ({t_cnt} orders)\n"
               f"🗓️ Last 7 days: <b>{fmt_money(w_sum)}</b> ({w_cnt} orders)\n"
               f"🗓️ Last 30 days: <b>{fmt_money(m_sum)}</b> ({m_cnt} orders)",
               kb([[btn("📄 Export Orders CSV", "export_orders", "primary")], [btn("⬅️ Back", "back:admin")]]))


async def export_orders(query, context):
    orders = db.get_all_orders(100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["order_id", "date", "product", "plan", "price", "username", "telegram_id", "key"])
    for o in orders:
        w.writerow([o["order_id"], fmt_time(o["created_at"]), o["product_name"], o["plan_name"],
                    o["price"], o.get("username") or "", o["telegram_id"], o.get("key_code") or ""])
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    await context.bot.send_document(
        query.message.chat_id, InputFile(data, filename=f"orders_{datetime.now():%Y%m%d}.csv"),
        caption=f"📄 {len(orders)} orders")
    db.log_admin(query.from_user.id, "exported orders CSV")


async def show_admin_users(query):
    text = "👥 <b>Recent Users</b>\n\n"
    for u in db.get_all_users(limit=20):
        flag = "🚫 " if u["banned"] else ""
        text += (f"{flag}🆔 <code>{u['telegram_id']}</code> | {uname(u)}\n"
                 f"   💰 {fmt_money(u['balance'])} | 📦 {u['total_orders']} orders\n\n")
    await edit(query, text, back_btn("admin"))


async def show_admin_orders(query):
    text = "📦 <b>Recent Orders</b>\n\n"
    for o in db.get_all_orders(limit=15):
        text += (f"🆔 <code>#{o['order_id']}</code> | {esc(o['product_name'])}\n"
                 f"   👤 {uname(o)} | 💰 {fmt_money(o['price'])} | {fmt_time(o['created_at'], '%d %b %H:%M')}\n\n")
    await edit(query, text, back_btn("admin"))


def user_card(u: Dict) -> str:
    return (f"👤 <b>User</b>\n\n🆔 <code>{u['telegram_id']}</code>\n👤 {uname(u)}\n"
            f"💰 Balance: <b>{fmt_money(u['balance'])}</b>\n📦 Orders: <b>{u['total_orders']}</b>\n"
            f"📅 Joined: {fmt_time(u['created_at'], '%d %b %Y')}\n"
            f"Status: {'🚫 <b>BANNED</b>' if u['banned'] else '✅ Active'}")


async def toggle_ban(query, context, tg_id: int):
    u = db.get_user(tg_id)
    if not u:
        await edit(query, "❌ User not found.", back_btn("admin"))
        return
    if is_admin(tg_id):
        await edit(query, "❌ You can't ban an admin.", back_btn("admin"))
        return
    new_state = not u["banned"]
    db.set_banned(tg_id, new_state)
    db.log_admin(query.from_user.id, f"{'banned' if new_state else 'unbanned'} user {tg_id}")
    u["banned"] = int(new_state)
    await edit(query, user_card(u),
               kb([[btn("✅ Unban" if new_state else "🚫 Ban", f"ban_toggle:{tg_id}",
                        "success" if new_state else "danger")],
                   [btn("⬅️ Back", "back:admin")]]))


async def show_admin_products(query):
    products = db.get_products(include_inactive=True)
    text = "🛍️ <b>Products</b>\n\n"
    rows = []
    for p in products:
        on = p["status"] == "active"
        text += f"{'🟢' if on else '🔴'} <b>{esc(p['name'])}</b> {'🔑' if p['use_api'] else '👑'}\n"
        rows.append([btn(f"✏️ {p['name'][:16]}", f"edit_prod:{p['id']}", "primary"),
                     btn("🙈 Hide" if on else "👁 Show", f"toggle_prod:{p['id']}"),
                     btn("🗑️", f"del_prod:{p['id']}", "danger")])
    rows.append([btn("➕ Add Product", "admin_add_product", "success")])
    rows.append([btn("⬅️ Back", "back:admin")])
    await edit(query, text, kb(rows))


PROD_FIELDS = {   # field: (button label, db column, optional?)
    "name": ("✏️ Name / Title", "name", False),
    "desc": ("📝 Description", "description", True),
    "vip_link": ("👑 VIP Channel Link", "vip_link", True),
    "vip_title": ("🏷 VIP Button Title", "vip_title", True),
    "demo_link": ("🎬 Demo Channel Link", "demo_link", True),
    "demo_title": ("🏷 Demo Button Title", "demo_title", True),
}
LINK_FIELDS = ("vip_link", "demo_link")


def edit_product_view(product_id: int):
    """-> (text, keyboard) of the product edit screen."""
    p = db.get_product(product_id)
    if not p:
        return "❌ Product not found.", back_btn("admin_products")
    on = p["status"] == "active"
    n_main = db.count_media(f"p{product_id}:main")
    n_demo = db.count_media(f"p{product_id}:demo")
    vip = esc(p["vip_link"]) if p.get("vip_link") else "<i>not set</i>"
    demo = esc(p["demo_link"]) if p.get("demo_link") else "<i>not set</i>"
    text = (head("✏️", esc(p["name"])) +
            f"Status: <b>{'🟢 Visible' if on else '🔴 Hidden'}</b>\n"
            f"Delivery: <b>{'🔑 API Key' if p['use_api'] else '👑 VIP Link'}</b>\n"
            f"👑 VIP link: {vip}\n🎬 Demo link: {demo}\n"
            f"🖼 Product media: <b>{n_main}</b> | 📸 Demo media: <b>{n_demo}</b>\n\n"
            f"Tap a button to edit 👇")
    pid = product_id
    rows = [
        [btn("✏️ Name / Title", f"admin_pf:{pid}:name", "primary"),
         btn("📝 Description", f"admin_pf:{pid}:desc", "primary")],
        [btn("👑 VIP Link", f"admin_pf:{pid}:vip_link", "success"),
         btn("🏷 VIP Btn Title", f"admin_pf:{pid}:vip_title", "primary")],
        [btn("🎬 Demo Link", f"admin_pf:{pid}:demo_link", "success"),
         btn("🏷 Demo Btn Title", f"admin_pf:{pid}:demo_title", "primary")],
        [btn(f"🖼 Product Media ({n_main})", f"admin_mm:p{pid}:main", "primary"),
         btn(f"📸 Demo Media ({n_demo})", f"admin_mm:p{pid}:demo", "primary")],
        [btn("🔁 Switch to VIP Link" if p["use_api"] else "🔁 Switch to API Key", f"admin_api:{pid}", "danger")],
    ]
    for pl in db.get_product_plans(pid):
        rows.append([btn(f"✏️ {pl['plan_name']} - {fmt_money(pl['price'])}", f"edit_plan:{pl['id']}", "primary"),
                     btn("🗑️", f"del_plan:{pl['id']}", "danger")])
    rows.append([btn("➕ Add Plan", f"add_plan:{pid}", "success")])
    rows.append([btn("⬅️ Back", "back:admin_products", "primary")])
    return text, kb(rows)


async def show_edit_product(query, product_id: int):
    text, markup = edit_product_view(product_id)
    await edit(query, text, markup)


def prod_field_prompt(p: Dict, field: str) -> str:
    label, col, optional = PROD_FIELDS[field]
    cur = p.get(col)
    shown = esc(str(cur)) if cur else "<i>not set</i>"
    hint = {
        "name": "Send the new name. You can use many emojis 🔥👑💎",
        "desc": "Send the new description.",
        "vip_link": "Send the VIP channel link, e.g. <code>https://t.me/+AbCd...</code>\nBuyers receive it after payment.",
        "vip_title": "Send the text for the VIP link button, e.g. <code>👑 Join VIP Channel</code>",
        "demo_link": "Send the demo channel link, e.g. <code>https://t.me/yourdemo</code>",
        "demo_title": "Send the text for the demo button, e.g. <code>🎬 Watch Demo</code>",
    }[field]
    tail = "\n\nSend <code>-</code> to remove it." if optional else ""
    return head("✏️", esc(label)) + f"Current: {shown}\n\n{hint}{tail}"


async def show_plan_menu(query, plan_id: int):
    plan = db.get_plan(plan_id)
    if not plan:
        await edit(query, "❌ Plan not found.", back_btn("admin_products"))
        return
    dur = fmt_duration(plan.get("api_hours")) or "not set"
    await edit(query,
               head("✏️", esc(plan["plan_name"])) +
               f"💰 Price: <b>{fmt_money(plan['price'])}</b>\n⏳ Duration: <b>{dur}</b>\n\nWhat do you want to change?",
               kb([[btn("💰 Change Price", f"admin_plan_price:{plan_id}", "success"),
                    btn("⏱ Name / Days", f"admin_plan_name:{plan_id}", "primary")],
                   [btn("⬅️ Back", f"edit_prod:{plan['product_id']}", "primary")]]))


# ─── admin: settings screen ───
def fmt_setting(key: str) -> str:
    _label, kind, var = SETTING_DEFS[key]
    v = globals()[var]
    if kind == "upi":
        return f"<code>{esc(str(v))}</code>"
    if kind == "key":
        s = str(v or "")
        return f"<code>{esc(s[:6] + '…' + s[-4:])}</code>" if len(s) > 12 else "<b>not set</b>"
    if kind == "url":
        return f"<code>{esc(str(v))}</code>" if v else "<b>not set</b> (QR photo is used)"
    if kind in ("link", "chat"):
        return f"<code>{esc(str(v))}</code>" if v else "<b>not set</b>"
    if kind == "num":
        return f"<b>{fmt_money(v)}</b>"
    if kind == "pct":
        return f"<b>{float(v):g}%</b>"
    return f"<b>{esc(str(v))}</b>"


def paymode_label() -> str:
    return "🤖 AUTO (API verifies)" if PAY_MODE == "auto" else "✋ MANUAL (admin approves)"


def settings_view():
    keys = list(SETTING_DEFS)
    text = head("⚙️", "Bot Settings")
    text += f"💳 Payment Mode: <b>{paymode_label()}</b>\n"
    for k in keys:
        text += f"{SETTING_DEFS[k][0]}: {fmt_setting(k)}\n"
    text += ("\nTap a button to change it 👇\nA new UPI ID, API key or mode is used for all new payments immediately.\n"
             "🌐 Pay Page URL = https address of this bot's web server (Mini App payment page).")
    rows = [[btn(f"💳 Mode: {'AUTO' if PAY_MODE == 'auto' else 'MANUAL'} (tap to switch)", "admin_paymode:set",
                 "success" if PAY_MODE == "auto" else "danger")]]
    rows += [[btn(SETTING_DEFS[k][0], f"admin_set:{k}", "primary") for k in keys[i:i + 2]]
             for i in range(0, len(keys), 2)]
    rows.append([btn("⬅️ Back", "back:admin", "primary")])
    return text, kb(rows)


async def show_admin_settings(query):
    text, markup = settings_view()
    await edit(query, text, markup)


def setting_prompt(key: str) -> str:
    label, kind, _var = SETTING_DEFS[key]
    hints = {
        "upi": "Send the new UPI ID, e.g. <code>name@bank</code>",
        "text": "Send the new store name (max 40 characters)",
        "user": "Send the support username, e.g. <code>@yourname</code>",
        "num": "Send the new amount (number)",
        "pct": "Send the percent (0 to 50)",
        "key": "Send the new FMPay API key. The base URL stays the same. Your message is deleted after saving.",
        "url": "Send the https address of the pay page, e.g. <code>https://pay.yourdomain.com</code>",
        "link": "Send the proof channel link users will open, e.g. <code>https://t.me/yourproofs</code>",
        "chat": ("Send the proof channel chat ID (<code>-100...</code>) or <code>@username</code>.\n"
                 "Add this bot to that channel as <b>admin</b> with permission to post messages."),
    }
    return (head("⚙️", "Change Setting") + f"{label}\nCurrent: {fmt_setting(key)}\n\n{hints[kind]}\n"
            "Send <code>-</code> to go back to the default value.")


# ─── admin: media manager (welcome screen / product / demo) ───
def media_title(owner: str) -> str:
    if owner == "welcome":
        return "Welcome Photos / Videos"
    if owner == "proof":
        return "Proof Photos / Videos"
    m = re.match(r"p(\d+):(main|demo)$", owner)
    if m:
        p = db.get_product(int(m.group(1)))
        name = esc(p["name"]) if p else "Product"
        return f"{name} - {'Product' if m.group(2) == 'main' else 'Demo'} Media"
    return "Media"


def owner_back(owner: str) -> str:
    if owner == "proof":
        return "admin_proof"
    m = re.match(r"p(\d+):", owner)
    return f"edit_prod:{m.group(1)}" if m else "back:admin"


def media_manager_view(owner: str):
    items = db.get_media(owner)
    photos = sum(1 for m in items if m["media_type"] == "photo")
    videos = sum(1 for m in items if m["media_type"] == "video")
    other = len(items) - photos - videos
    text = (head("🖼", media_title(owner)) +
            f"📷 Photos: <b>{photos}</b>\n🎥 Videos: <b>{videos}</b>\n"
            + (f"🎞 GIF / files: <b>{other}</b>\n" if other else "") + "\n"
            f"Users see these in the order you added them (max {MAX_MEDIA_PER_ITEM}).")
    rows = [[btn("➕ Add Photos / Videos", f"admin_madd:{owner}", "success")]]
    if items:
        rows.append([btn("👁 Preview", f"admin_mview:{owner}", "primary"),
                     btn("🗑 Clear All", f"admin_mclear:{owner}", "danger")])
    rows.append([btn("⬅️ Back", owner_back(owner), "primary")])
    return text, kb(rows)


async def show_media_manager(query, owner: str):
    text, markup = media_manager_view(owner)
    await edit(query, text, markup)


async def preview_media(query, context, owner: str):
    items = db.get_media(owner)
    if not items:
        await show_media_manager(query, owner)
        return
    chat_id = query.message.chat_id
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_media(context.bot, chat_id, items)
    text, markup = media_manager_view(owner)
    await context.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


async def show_admin_coupons(query):
    text = head("🎁", "Coupons")
    rows = []
    coupons = db.get_all_coupons()
    if not coupons:
        text += "No coupons yet."
    for cp in coupons:
        exp = fmt_time(cp.get("expiry_date"), "%d %b %Y") if cp.get("expiry_date") else "No expiry"
        val = f"{cp['value']:g}%" if cp["type"] == "PERCENT" else fmt_money(cp["value"])
        text += (f"🏷️ <b>{esc(cp['code'])}</b> | {cp['type']} | {val}\n"
                 f"   Used: {cp['used_count']}/{cp['usage_limit']} | Exp: {exp}\n\n")
        rows.append([btn(f"🗑️ Delete {cp['code']}", f"del_coupon:{cp['id']}", "danger")])
    rows.append([btn("➕ Add Coupon", "admin_add_coupon", "success")])
    rows.append([btn("⬅️ Back", "back:admin", "primary")])
    await edit(query, text, kb(rows))


# ═══════════════════════════════════════════════════════════════════
# BROADCAST (text / photo / video + buttons)
# ═══════════════════════════════════════════════════════════════════

_bg_tasks: set = set()


def bc_buttons_prompt() -> str:
    return (head("📢", "Broadcast Buttons") + "<b>Step 2/2:</b> Add buttons under the message.\n\n"
            "One button per line:\n<code>Button text | link</code>\n\n"
            "Two buttons on one row: join them with <code>&amp;&amp;</code>\n"
            "<code>🛒 Buy Now | BOT</code>\n"
            "<code>🧾 Proofs | https://t.me/yourproofs &amp;&amp; 📞 Support | @yourname</code>\n\n"
            "• <code>BOT</code> = link to this bot (opens the bot on tap)\n"
            "• Max 6 rows\n\nSend <code>-</code> for no buttons.")


def parse_bc_buttons(text: str, bot_username: str):
    rows, errs = [], []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        row = []
        for part in line.split("&&"):
            part = part.strip()
            if "|" not in part:
                errs.append(part)
                continue
            label, link = part.rsplit("|", 1)
            label, link = label.strip(), link.strip()
            url = f"https://t.me/{bot_username}" if link.upper() == "BOT" else norm_link(link)
            if not label or not url:
                errs.append(part)
                continue
            row.append((label[:40], url))
        if row:
            rows.append(row)
    return rows[:6], errs


def bc_markup(bc: Dict) -> Optional[InlineKeyboardMarkup]:
    if not bc.get("buttons"):
        return None
    return kb([[btn(t, url=u, style="primary") for t, u in row] for row in bc["buttons"]])


async def send_bc(bot, chat_id: int, bc: Dict):
    """Sends one broadcast message (used for the preview and for every user)."""
    body = f"📢 <b>Announcement from {esc(STORE_NAME)}</b>\n{DIV}\n{bc['html']}"
    markup = bc_markup(bc)
    media = bc.get("media")
    if not media:
        return await bot.send_message(chat_id, RAW + body, reply_markup=markup, parse_mode="HTML", keep_page=True)
    fid, mtype = media
    if len(body) <= 1000:
        if mtype == "video":
            return await bot.send_video(chat_id, fid, caption=body, reply_markup=markup, parse_mode="HTML")
        return await bot.send_photo(chat_id, fid, caption=RAW + body, reply_markup=markup,
                                    parse_mode="HTML", keep_page=True)
    if mtype == "video":   # long text: media first, text with buttons after it
        await bot.send_video(chat_id, fid)
    else:
        await bot.send_photo(chat_id, fid)
    return await bot.send_message(chat_id, RAW + body, reply_markup=markup, parse_mode="HTML", keep_page=True)


async def run_broadcast(bot, admin_chat: int, admin_id: int, bc: Dict):
    sent = failed = 0
    for u in db.get_all_users(limit=100000):
        if u["banned"]:
            continue
        try:
            await send_bc(bot, u["telegram_id"], bc)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    db.log_admin(admin_id, f"broadcast sent={sent} failed={failed}")
    try:
        await bot.send_message(admin_chat, f"✅ Broadcast complete!\nSent: {sent}\nFailed: {failed}",
                               reply_markup=main_menu(admin_id), parse_mode="HTML")
    except Exception:
        pass


async def start_broadcast(context, query):
    bc = context.user_data.pop("bc", None)
    if not bc:
        await edit(query, "❌ Nothing to send. Start the broadcast again.", back_btn("admin"))
        return
    await edit(query, "📢 <b>Broadcast started.</b>\n\nYou will get a report when it finishes.", back_btn("admin"))
    task = asyncio.create_task(run_broadcast(context.bot, query.message.chat_id, query.from_user.id, bc))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# ═══════════════════════════════════════════════════════════════════
# PROOF  (user button + anonymous channel posts + admin screen) / PAYMENT REMINDER
# ═══════════════════════════════════════════════════════════════════

async def show_proof(query, context):
    if not proof_active():
        await edit(query, "🧾 Proofs are not available right now.", back_btn("main"))
        return
    text = (head("🧾", "Payment Proofs") +
            "See real orders from our verified buyers.\n\n"
            "✅ Every order is delivered\n🔒 Buyer identity is always hidden\n\n"
            "Tap below to open the proof channel 👇")
    rows = []
    if PROOF_LINK:
        rows.append([btn("📢 Open Proof Channel", url=PROOF_LINK, style="success")])
    rows.append([btn("⬅️ Back", "back:main", "primary")])
    media = db.get_media("proof")
    if media:
        chat_id = query.message.chat_id
        try:
            await query.message.delete()
        except Exception:
            pass
        await send_media(context.bot, chat_id, media)
        await context.bot.send_message(chat_id, text, reply_markup=kb(rows), parse_mode="HTML")
    else:
        await edit(query, text, kb(rows))


def proof_chat_id():
    c = str(PROOF_CHAT or "").strip()
    if not c:
        return None
    return int(c) if re.fullmatch(r"-?\d+", c) else c


_proof_err_at = 0.0


async def post_proof(bot, product: Dict, plan: Dict, price: float, test: bool = False) -> Optional[str]:
    """Posts an anonymous 'verified order' card to the proof channel. Returns an error text, or None if posted."""
    chat = proof_chat_id()
    if not chat:
        return "Proof channel ID is not set."
    try:
        me = await bot.get_me()
        text = ""
        if test:
            text += "🧪 <b>TEST POST</b> (only to check the channel)\n"
        text += (f"✅ <b>NEW ORDER VERIFIED</b>\n{DIV}\n"
                 f"🎮 Product: <b>{esc(product['name'])}</b>\n"
                 f"📋 Plan: <b>{esc(plan['plan_name'])}</b>\n")
        dur = fmt_duration(plan.get("api_hours"))
        if dur:
            text += f"⏳ Validity: <b>{dur}</b>\n"
        text += (f"💰 Amount: <b>{fmt_money(price)}</b>\n"
                 f"👤 Buyer: <b>Verified Buyer ✔️</b> (identity hidden)\n"
                 f"📅 Date: <b>{fmt_time(utc_now_str())}</b>\n{DIV}\n"
                 f"🚀 Tap <b>Buy Now</b> to order from <b>{esc(STORE_NAME)}</b>")
        await bot.send_message(chat, text, parse_mode="HTML", keep_page=True,
                               reply_markup=kb([[btn("🛒 Buy Now", url=f"https://t.me/{me.username}", style="success")]]))
        return None
    except Exception as e:
        logger.warning(f"post_proof failed: {e}")
        return str(e)[:200]


async def auto_post_proof(bot, product: Dict, plan: Dict, price: float):
    """Called after every completed order. Never raises; tells the admins about a problem at most once an hour."""
    global _proof_err_at
    if not (flag("proof_post") and PROOF_CHAT):
        return
    err = await post_proof(bot, product, plan, price)
    if err and time.time() - _proof_err_at > 3600:
        _proof_err_at = time.time()
        await notify_admins(bot, f"⚠️ <b>Proof post failed</b>\n\n<code>{esc(err)}</code>\n\n"
                                 f"Check that the bot is admin in the proof channel and the channel ID is right "
                                 f"(Admin → Proof / Reminder).")


def proof_admin_view():
    on, post, rem = flag("proof_on"), flag("proof_post"), flag("remind_on")
    n = db.count_media("proof")
    st = lambda v: "ON 🟢" if v else "OFF 🔴"
    text = (head("🧾", "Proof / Reminder") +
            f"🧾 Proof button: <b>{st(on)}</b>\n"
            f"📡 Channel auto-post: <b>{st(post)}</b>\n"
            f"⏰ Payment reminder: <b>{st(rem)}</b> (after {REMIND_AFTER_MIN} min)\n\n"
            f"📢 Proof link (users open it): {fmt_setting('proof_link')}\n"
            f"📡 Channel ID (bot posts here): {fmt_setting('proof_chat')}\n"
            f"🖼 Proof media on the Proof page: <b>{n}</b>\n\n"
            "The proof button shows on the main page when it is ON and a link or media is set.\n"
            "Add the bot as <b>admin</b> in your channel so it can post. Buyer identity is never shown.")
    rows = [
        [btn(f"🧾 Proof Button: {'ON' if on else 'OFF'}", "admin_flag:proof_on", "success" if on else "danger")],
        [btn(f"📡 Channel Post: {'ON' if post else 'OFF'}", "admin_flag:proof_post", "success" if post else "danger")],
        [btn(f"⏰ Reminder: {'ON' if rem else 'OFF'}", "admin_flag:remind_on", "success" if rem else "danger")],
        [btn("🔗 Proof Link", "admin_set:proof_link", "primary"), btn("📡 Channel ID", "admin_set:proof_chat", "primary")],
        [btn(f"🖼 Proof Media ({n})", "admin_mm:proof", "primary")],
        [btn("🧪 Send Test Post", "admin_proof_test", "success")],
        [btn("⬅️ Back", "back:admin", "primary")],
    ]
    return text, kb(rows)


async def show_admin_proof(query):
    text, markup = proof_admin_view()
    await edit(query, text, markup)


async def admin_proof_test(query, context):
    prods = db.get_products()
    product, plan = {"name": "Sample Product"}, {"plan_name": "7 Days", "api_hours": 168, "price": 100}
    if prods:
        pls = db.get_product_plans(prods[0]["id"])
        if pls:
            product, plan = prods[0], pls[0]
    err = await post_proof(context.bot, product, plan, float(plan["price"]), test=True)
    msg = ("✅ <b>Test post sent.</b> Check your channel." if not err else
           f"❌ <b>Could not post.</b>\n\n<code>{esc(err)}</code>\n\n"
           "Make sure the channel ID is right and the bot is an admin of that channel.")
    await edit(query, msg, kb([[btn("⬅️ Back", "admin_proof", "primary")]]))


async def remind_job(context: ContextTypes.DEFAULT_TYPE):
    """Gentle nudge for payments that were started but not finished."""
    if not flag("remind_on"):
        return
    for t in db.get_due_reminders(REMIND_AFTER_MIN, PAYMENT_EXPIRY_MIN):
        db.mark_reminded(t["id"])   # only ever one reminder per payment
        if t.get("banned"):
            continue
        left_min = tx_seconds_left(t) // 60
        if left_min < 1:
            continue
        try:
            page = pay_page_url(t["id"])
            rows = []
            if page:
                rows.append([btn("💳 Pay Now", style="success", web_app=page)])
            rows.append([btn("✅ I Paid - Enter UTR", f"utr_verify:{t['id']}", "primary")])
            if proof_active() and PROOF_LINK:
                rows.append([btn("🧾 See Payment Proofs", url=PROOF_LINK, style="success")])
            rows.append(cancel_row())
            text = (head("⏰", "Payment Reminder") +
                    f"Your payment of <b>{fmt_money(t['amount'])}</b> is still pending.\n"
                    f"🧾 Ref: <code>{esc(t.get('order_id') or '')}</code>\n"
                    f"⌛ Session ends in about <b>{left_min} min</b>\n\n"
                    "Finish it now and your balance is added right after verification.\n"
                    "Already paid? Tap <b>I Paid</b> and send your UTR / Transaction ID.")
            sent = await context.bot.send_message(t["telegram_id"], text, reply_markup=kb(rows), parse_mode="HTML")
            old = t.get("message_id")
            db.set_tx_message(t["id"], sent.message_id)
            if old and old != sent.message_id and t.get("chat_id"):
                try:
                    await context.bot.delete_message(t["chat_id"], old)
                except Exception:
                    pass
        except Exception as e:
            logger.info(f"reminder for tx {t['id']} not sent: {e}")


# ═══════════════════════════════════════════════════════════════════
# MESSAGE HANDLER (text input states)
# ═══════════════════════════════════════════════════════════════════

ADMIN_STATES = {"await_prod_name", "await_prod_group", "await_prod_plans", "await_prod_desc",
                "await_coupon_code", "await_broadcast", "await_admin_user_id", "await_admin_amount",
                "await_search", "await_plan_price", "await_welcome", "await_new_plan",
                "await_prod_vip", "await_prod_demo", "await_prod_field", "await_plan_name",
                "await_setting", "await_media", "await_bc_buttons"}


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    uid = tg.id
    if not is_admin(uid) and flooded(uid, 0.6):
        return
    user = ensure_user(tg)
    blocked = gate_text(user, uid)
    if blocked:
        await update.message.reply_text(blocked, reply_markup=gate_markup(user))
        return

    msg = update.message
    text = (msg.text or "").strip()
    state = context.user_data.get("state")

    if state in ADMIN_STATES and not is_admin(uid):
        context.user_data.clear()
        return

    # ─── wallet ───
    if state == "await_amount":
        try:
            amount = round(float(text), 2)
        except ValueError:
            await msg.reply_text("❌ Please enter a valid number.")
            return
        if amount < MIN_RECHARGE or amount > MAX_RECHARGE:
            await msg.reply_text(f"❌ Amount must be between {fmt_money(MIN_RECHARGE)} and {fmt_money(MAX_RECHARGE)}.")
            return
        context.user_data.pop("state", None)
        await send_payment_details(context, msg.chat_id, amount, user)

    elif state == "await_utr":
        tx_id = context.user_data.get("utr_tx_id")
        utr = norm_txn_id(text)
        if not utr:
            await msg.reply_text("❌ Invalid ID. Send the <b>12-digit UTR</b> (any UPI app) or the "
                                 "<b>FamPay Transaction ID</b>. Please send it again:", parse_mode="HTML")
            return
        context.user_data.clear()
        await verify_by_utr(context, msg, user, tx_id, utr)

    # ─── v1 android id ───
    elif state == "await_android_id":
        pending = context.user_data.get("pending_purchase")
        if not pending:
            await msg.reply_text("❌ Session expired.", reply_markup=main_menu(uid))
            return
        product = db.get_product(pending["product_id"])
        plan = db.get_plan(pending["plan_id"])
        if not product or not plan:
            await msg.reply_text("❌ Product not available.", reply_markup=main_menu(uid))
            return
        android_id = text[:64]
        context.user_data.pop("state", None)
        context.user_data.pop("pending_purchase", None)
        context.user_data["android_id"] = android_id
        price, discount, code = price_with_coupon(user, float(plan["price"]))
        await msg.reply_text(
            order_summary(product, plan, price, discount, code, float(user["balance"]), android_id),
            reply_markup=kb([[btn("✅ Confirm", f"confirm_buy:{product['id']}:{plan['id']}", "success")],
                             cancel_row()]),
            parse_mode="HTML")

    # ─── coupon redeem ───
    elif state == "await_coupon":
        context.user_data.pop("state", None)
        status, c = db.redeem_coupon(text, user["id"])
        if status == "invalid":
            await msg.reply_text("❌ Invalid or expired coupon.", reply_markup=main_menu(uid))
        elif status == "used":
            await msg.reply_text("❌ You have already used this coupon.", reply_markup=main_menu(uid))
        elif status == "limit":
            await msg.reply_text("❌ This coupon has reached its usage limit.", reply_markup=main_menu(uid))
        elif c["type"] == "BALANCE":
            db.update_balance(uid, float(c["value"]))
            await msg.reply_text(f"🎁 <b>Coupon Redeemed!</b>\n\n💰 {fmt_money(c['value'])} added to your wallet!",
                                 reply_markup=main_menu(uid), parse_mode="HTML")
        else:
            db.set_active_coupon(uid, c["code"])
            val = f"{c['value']:g}%" if c["type"] == "PERCENT" else fmt_money(c["value"])
            await msg.reply_text(
                f"🎁 Coupon <b>{esc(c['code'])}</b> activated!\n\n🏷️ <b>{val} OFF</b> will be applied on your "
                f"next purchase automatically.", reply_markup=main_menu(uid), parse_mode="HTML")

    # ─── admin: add product ───
    elif state == "await_prod_name":
        context.user_data["prod_name"] = text[:60]
        context.user_data["state"] = "await_prod_plans"
        await msg.reply_text(
            f"<b>Step 2/5:</b> Send the plans for <b>{esc(text[:60])}</b>\n\n"
            f"One per line, format <code>Name = Price</code>\n"
            f"<code>7 Days = 100</code>\n<code>30 Days = 300</code>\n<code>Lifetime = 999</code>\n\n"
            f"The plan name sets how long the VIP stays (Hours / Days / Weeks / Months / Lifetime).",
            reply_markup=kb([cancel_row()]), parse_mode="HTML")

    elif state == "await_prod_plans":
        plans = []
        errors = []
        for line in text.split("\n"):
            if "=" not in line:
                continue
            name, price = line.split("=", 1)
            name = name.strip()
            try:
                price_v = float(price.strip())
                if price_v <= 0:
                    raise ValueError
            except ValueError:
                errors.append(line)
                continue
            hours = guess_hours(name)
            if hours is None:
                errors.append(f"{line}  (no duration in name, e.g. 30 Days / 1 Month / Lifetime)")
                continue
            plans.append((name, price_v, hours))
        if errors or not plans:
            await msg.reply_text("❌ Fix these lines and send again:\n" +
                                 "\n".join(esc(e) for e in (errors or ["No valid plans found."])) +
                                 "\n\nFormat: Name = Price  (e.g. 2 Days = 500)")
            return
        context.user_data["prod_plans"] = plans
        context.user_data["state"] = "await_prod_desc"
        await msg.reply_text("<b>Step 3/5:</b> Send the product description (or <code>-</code> to skip):",
                             reply_markup=kb([cancel_row()]), parse_mode="HTML")

    elif state == "await_prod_desc":
        context.user_data["prod_desc"] = "" if text == "-" else text[:900]
        context.user_data["state"] = "await_prod_vip"
        await msg.reply_text(
            "<b>Step 4/5:</b> Send the <b>VIP channel link</b> that buyers get after payment\n"
            "(e.g. <code>https://t.me/+AbCdEf...</code>)\n\nSend <code>-</code> to skip (you can set it later).",
            reply_markup=kb([cancel_row()]), parse_mode="HTML")

    elif state == "await_prod_vip":
        link = None
        if text != "-":
            link = norm_link(text)
            if not link:
                await msg.reply_text("❌ Send a valid link (https://t.me/...) or <code>-</code> to skip.",
                                     parse_mode="HTML")
                return
        context.user_data["prod_vip"] = link
        context.user_data["state"] = "await_prod_demo"
        await msg.reply_text("<b>Step 5/5:</b> Send the <b>demo channel link</b> (or <code>-</code> to skip):",
                             reply_markup=kb([cancel_row()]), parse_mode="HTML")

    elif state == "await_prod_demo":
        demo = None
        if text != "-":
            demo = norm_link(text)
            if not demo:
                await msg.reply_text("❌ Send a valid link (https://t.me/...) or <code>-</code> to skip.",
                                     parse_mode="HTML")
                return
        data = context.user_data
        name = data["prod_name"]
        pid = db.create_product(name, "Main", data.get("prod_desc", ""), use_api=0,
                                vip_link=data.get("prod_vip"), demo_link=demo)
        for plan_name, price, hours in data["prod_plans"]:
            db.add_product_plan(pid, plan_name, price, hours)
        db.log_admin(uid, f"created product {pid} {name}")
        context.user_data.clear()
        await msg.reply_text(f"✅ Product <b>{esc(name)}</b> created - it is now on the main page!\n\n"
                             f"Add photos/videos, demo media or button titles below 👇", parse_mode="HTML")
        view_text, view_markup = edit_product_view(pid)
        await msg.reply_text(view_text, reply_markup=view_markup, parse_mode="HTML")

    elif state == "await_plan_price":
        try:
            price = round(float(text), 2)
            if price <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ Send a valid price (number greater than 0).")
            return
        plan_id = context.user_data.get("edit_plan_id")
        db.update_plan_price(plan_id, price)
        db.log_admin(uid, f"plan {plan_id} price -> {price}")
        context.user_data.clear()
        await msg.reply_text(f"✅ Price updated to <b>{fmt_money(price)}</b>.", parse_mode="HTML")
        plan = db.get_plan(plan_id)
        if plan:
            view_text, view_markup = edit_product_view(plan["product_id"])
            await msg.reply_text(view_text, reply_markup=view_markup, parse_mode="HTML")

    # ─── admin: coupon ───
    elif state == "await_coupon_code":
        context.user_data.pop("state", None)
        try:
            parts = text.split()
            if len(parts) < 3:
                raise ValueError("Invalid format")
            code, type_, value = parts[0].upper(), parts[1].upper(), float(parts[2])
            if type_ not in ("BALANCE", "PERCENT", "FLAT"):
                raise ValueError("TYPE must be BALANCE, PERCENT or FLAT")
            if type_ == "PERCENT" and not 0 < value <= 90:
                raise ValueError("PERCENT must be between 1 and 90")
            days = int(parts[3]) if len(parts) > 3 else 30
            limit = int(parts[4]) if len(parts) > 4 else 1
            db.create_coupon(code, type_, value, days or None, limit)
            db.log_admin(uid, f"created coupon {code}")
            await msg.reply_text(f"✅ Coupon <b>{esc(code)}</b> created!",
                                 reply_markup=main_menu(uid), parse_mode="HTML")
        except sqlite3.IntegrityError:
            await msg.reply_text("❌ That coupon code already exists.", reply_markup=main_menu(uid))
        except Exception as e:
            await msg.reply_text(f"❌ {esc(str(e))}\nFormat: CODE TYPE VALUE DAYS LIMIT",
                                 reply_markup=main_menu(uid))

    # ─── admin: broadcast (text -> buttons -> preview -> send) ───
    elif state == "await_broadcast":
        context.user_data["bc"] = {"html": msg.text_html or esc(text), "media": None, "buttons": []}
        context.user_data["state"] = "await_bc_buttons"
        await msg.reply_text(bc_buttons_prompt(), reply_markup=kb([cancel_row()]), parse_mode="HTML")

    elif state == "await_bc_buttons":
        bc = context.user_data.get("bc")
        if not bc:
            context.user_data.clear()
            await msg.reply_text("❌ Broadcast draft lost. Start again.", reply_markup=main_menu(uid))
            return
        bot_username = (await context.bot.get_me()).username
        if text == "-":
            buttons = []
        else:
            buttons, errs = parse_bc_buttons(text, bot_username)
            if errs or not buttons:
                await msg.reply_text("❌ Fix these and send again:\n" +
                                     "\n".join(esc(e) for e in (errs or ["No valid button found."])) +
                                     "\n\nFormat: <code>Button text | link</code>", parse_mode="HTML")
                return
        bc["buttons"] = buttons
        context.user_data.pop("state", None)
        await msg.reply_text("👀 <b>Preview</b> - users will see this:", parse_mode="HTML")
        await send_bc(context.bot, msg.chat_id, bc)
        await msg.reply_text(
            f"Send this to <b>{db.count_users()}</b> users?",
            reply_markup=kb([[btn("🚀 Send Now", "admin_bc_send", "success")], cancel_row()]),
            parse_mode="HTML")

    # ─── admin: search / ban ───
    elif state == "await_search":
        context.user_data.pop("state", None)
        u = db.find_user(text)
        if not u:
            await msg.reply_text("❌ User not found.", reply_markup=main_menu(uid))
            return
        await msg.reply_text(
            user_card(u),
            reply_markup=kb([[btn("✅ Unban" if u["banned"] else "🚫 Ban", f"ban_toggle:{u['telegram_id']}",
                                  "success" if u["banned"] else "danger")],
                             [btn("⬅️ Back", "back:admin")]]),
            parse_mode="HTML")

    # ─── admin: add balance ───
    elif state == "await_admin_user_id":
        try:
            target = int(text)
        except ValueError:
            await msg.reply_text("❌ Please enter a valid numeric User ID.")
            return
        if not db.get_user(target):
            await msg.reply_text("❌ User not found (they must have started the bot).")
            return
        context.user_data["target_user_id"] = target
        context.user_data["state"] = "await_admin_amount"
        verb = "deduct" if context.user_data.get("bal_mode") == "deduct" else "add"
        await msg.reply_text(f"Enter amount to {verb}:", reply_markup=kb([cancel_row()]))

    elif state == "await_admin_amount":
        try:
            amount = abs(round(float(text), 2))
            if amount == 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ Please enter a valid amount.")
            return
        target = context.user_data.get("target_user_id")
        deduct = context.user_data.get("bal_mode") == "deduct"
        if deduct:
            target_user = db.get_user(target)
            amount = min(amount, float(target_user["balance"])) if target_user else 0
            if amount <= 0:
                context.user_data.clear()
                await msg.reply_text("❌ User has no balance to deduct.", reply_markup=main_menu(uid))
                return
            db.update_balance(target, -amount)
            db.log_admin(uid, f"deducted {amount} from {target}")
        else:
            db.update_balance(target, amount)
            db.log_admin(uid, f"added {amount} to {target}")
        context.user_data.clear()
        await msg.reply_text(
            f"✅ {'Deducted' if deduct else 'Added'} {fmt_money(amount)} "
            f"{'from' if deduct else 'to'} <code>{target}</code>",
            reply_markup=main_menu(uid), parse_mode="HTML")
        try:
            if deduct:
                await context.bot.send_message(
                    target, f"⚠️ <b>Balance Adjusted</b>\n\n💰 {fmt_money(amount)} was deducted from your wallet by admin.",
                    parse_mode="HTML")
            else:
                await context.bot.send_message(
                    target, f"🎉 <b>Balance Added!</b>\n\n💰 {fmt_money(amount)} was added to your wallet.",
                    parse_mode="HTML")
        except Exception:
            pass

    # ─── admin: welcome message ───
    elif state == "await_welcome":
        context.user_data.clear()
        if text == "-":
            db.set_setting("welcome_msg", None)
            db.log_admin(uid, "reset welcome message")
            await msg.reply_text("✅ Welcome message reset to default.", reply_markup=main_menu(uid))
        else:
            db.set_setting("welcome_msg", msg.text_html or esc(text))
            db.log_admin(uid, "updated welcome message")
            await msg.reply_text("✅ Welcome message updated!\n\nUse /start to preview it.",
                                 reply_markup=main_menu(uid))

    # ─── admin: edit one product field (name, description, VIP link/title, demo link/title) ───
    elif state == "await_prod_field":
        pid = context.user_data.get("prod_edit_id")
        field = context.user_data.get("prod_edit_field")
        spec = PROD_FIELDS.get(field)
        if not spec or not pid or not db.get_product(pid):
            context.user_data.clear()
            await msg.reply_text("❌ Product not found.", reply_markup=main_menu(uid))
            return
        label, col, optional = spec
        if text == "-" and optional:
            value = None
        elif text == "-":
            await msg.reply_text("❌ The name cannot be empty. Send the new name:")
            return
        elif field in LINK_FIELDS:
            value = norm_link(text)
            if not value:
                await msg.reply_text("❌ Send a valid link (https://t.me/...) or <code>-</code> to remove it.",
                                     parse_mode="HTML")
                return
        elif field == "name":
            value = text[:60]
        elif field in ("vip_title", "demo_title"):
            value = text[:40]
        else:
            value = text[:900]
        db.update_product_field(pid, col, value)
        db.log_admin(uid, f"product {pid} {col} updated")
        context.user_data.clear()
        await msg.reply_text(f"✅ {label} updated.")
        view_text, view_markup = edit_product_view(pid)
        await msg.reply_text(view_text, reply_markup=view_markup, parse_mode="HTML")

    # ─── admin: rename a plan / change its duration ───
    elif state == "await_plan_name":
        plan_id = context.user_data.get("plan_edit_id")
        plan = db.get_plan(plan_id) if plan_id else None
        if not plan:
            context.user_data.clear()
            await msg.reply_text("❌ Plan not found.", reply_markup=main_menu(uid))
            return
        hours = guess_hours(text)
        if hours is None:
            await msg.reply_text("❌ The name must contain a duration, e.g. <code>30 Days</code>, "
                                 "<code>1 Month</code> or <code>Lifetime</code>.", parse_mode="HTML")
            return
        db.update_plan_name(plan_id, text[:40], hours)
        db.log_admin(uid, f"plan {plan_id} renamed to {text[:40]}")
        context.user_data.clear()
        await msg.reply_text(f"✅ Plan updated: <b>{esc(text[:40])}</b> ({fmt_duration(hours)}).", parse_mode="HTML")
        view_text, view_markup = edit_product_view(plan["product_id"])
        await msg.reply_text(view_text, reply_markup=view_markup, parse_mode="HTML")

    # ─── admin: bot settings (UPI ID, store name, support, limits) ───
    elif state == "await_setting":
        key = context.user_data.get("setting_key")
        if key not in SETTING_DEFS:
            context.user_data.clear()
            return
        label, kind, _var = SETTING_DEFS[key]
        if text == "-":
            value = None
        else:
            value, err = validate_setting(kind, text, key)
            if err:
                await msg.reply_text(err, parse_mode="HTML")
                return
        db.set_setting(key, value)
        apply_settings()
        db.log_admin(uid, f"setting {key} -> {value if value is not None else 'default'}")
        context.user_data.clear()
        if kind == "key":   # keep the secret out of the chat history
            try:
                await msg.delete()
            except Exception:
                pass
        await msg.reply_text(f"✅ {label} updated.")
        if key == "upi_id":
            await notify_admins(context.bot, f"⚠️ <b>UPI ID changed</b>\n\nBy admin: <code>{uid}</code>\n"
                                             f"New UPI ID: <code>{esc(UPI_ID)}</code>")
        if key in ("proof_link", "proof_chat"):
            view_text, view_markup = proof_admin_view()
        else:
            view_text, view_markup = settings_view()
        await msg.reply_text(view_text, reply_markup=view_markup, parse_mode="HTML")

    # ─── admin: add plan to existing product ───
    elif state == "await_new_plan":
        pid = context.user_data.get("plan_product_id")
        if "=" not in text:
            await msg.reply_text("❌ Format: <code>Name = Price</code>  (e.g. <code>2 Days = 500</code>)")
            return
        name, price_s = text.split("=", 1)
        name = name.strip()
        hours = guess_hours(name)
        try:
            price = round(float(price_s.strip()), 2)
            if price <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ Send a valid price.")
            return
        if hours is None:
            await msg.reply_text("❌ Plan name must contain a duration (e.g. <code>2 Days</code>, "
                                 "<code>1 Month</code> or <code>Lifetime</code>).")
            return
        db.add_product_plan(pid, name, price, hours)
        db.log_admin(uid, f"added plan {name} = {price} to product {pid}")
        context.user_data.clear()
        await msg.reply_text(f"✅ Plan <b>{esc(name)}</b> added at {fmt_money(price)}.")
        view_text, view_markup = edit_product_view(pid)
        await msg.reply_text(view_text, reply_markup=view_markup, parse_mode="HTML")

    else:
        await msg.reply_text(welcome_text(user), reply_markup=main_menu(uid), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
# PHOTO / VIDEO UPLOAD  (welcome screen, product media, demo media)
# ═══════════════════════════════════════════════════════════════════

async def media_summary(bot, chat_id: int, owner: str, full: bool):
    items = db.get_media(owner)
    photos = sum(1 for m in items if m["media_type"] == "photo")
    videos = sum(1 for m in items if m["media_type"] == "video")
    other = len(items) - photos - videos
    line = (f"❌ Maximum {MAX_MEDIA_PER_ITEM} files reached." if full else "✅ Added!")
    await bot.send_message(
        chat_id,
        f"{line}\n\n📦 Total saved: <b>{len(items)}</b> / {MAX_MEDIA_PER_ITEM}\n"
        f"📷 {photos}  🎥 {videos}" + (f"  🎞 {other}" if other else "") +
        "\n\nSend more, or tap <b>Done</b>.",
        reply_markup=kb([[btn("✅ Done", f"admin_mm:{owner}", "success")]]), parse_mode="HTML")


async def media_summary_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await media_summary(context.bot, context.job.chat_id, d["owner"], d["full"])


async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sends photos / videos / GIFs / files. Everyone else is ignored."""
    msg = update.message
    if not msg or not update.effective_user:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    state = context.user_data.get("state")

    # broadcast with a photo / video (the caption is the message text)
    if state == "await_broadcast":
        if msg.photo:
            media = (msg.photo[-1].file_id, "photo")
        elif msg.video:
            media = (msg.video.file_id, "video")
        else:
            await msg.reply_text("❌ For a broadcast send text, a photo or a video.")
            return
        context.user_data["bc"] = {"html": msg.caption_html or "", "media": media, "buttons": []}
        context.user_data["state"] = "await_bc_buttons"
        await msg.reply_text(bc_buttons_prompt(), reply_markup=kb([cancel_row()]), parse_mode="HTML")
        return

    if state != "await_media":
        return
    owner = context.user_data.get("media_owner")
    if not owner:
        return
    if msg.photo:
        file_id, mtype = msg.photo[-1].file_id, "photo"
    elif msg.video:
        file_id, mtype = msg.video.file_id, "video"
    elif msg.animation:
        file_id, mtype = msg.animation.file_id, "animation"
    elif msg.document and (msg.document.mime_type or "").startswith(("video/", "image/")):
        file_id, mtype = msg.document.file_id, "document"
    else:
        return
    full = db.count_media(owner) >= MAX_MEDIA_PER_ITEM
    if not full:
        db.add_media(owner, file_id, mtype)
    # one summary per upload: wait until the last file of an album has arrived
    chat_id = msg.chat_id
    jq = context.job_queue
    if jq:
        for j in jq.get_jobs_by_name(f"msum{chat_id}"):
            j.schedule_removal()
        jq.run_once(media_summary_job, 1.5, chat_id=chat_id, data={"owner": owner, "full": full},
                    name=f"msum{chat_id}")
    else:
        await media_summary(context.bot, chat_id, owner, full)


# ═══════════════════════════════════════════════════════════════════
# PAYMENT ENGINE  (UTR verification + UTR registry)
# ═══════════════════════════════════════════════════════════════════

_utr_fails: Dict[int, List[float]] = {}


def utr_blocked(uid: int) -> bool:
    """Max 5 failed UTR checks per 10 minutes (stops guessing)."""
    now = time.time()
    recent = [t for t in _utr_fails.get(uid, []) if now - t < 600]
    _utr_fails[uid] = recent
    return len(recent) >= 5


async def drop_qr_message(bot, tx: Dict):
    if tx.get("chat_id") and tx.get("message_id"):
        try:
            await bot.delete_message(tx["chat_id"], tx["message_id"])
        except Exception:
            pass


async def finalize_payment(bot, tx: Dict, utr: Optional[str], by_admin: Optional[int] = None) -> str:
    """Registers the UTR + credits the wallet (once) and notifies user and admins."""
    res = db.credit_payment(tx["id"], utr)
    u = db.get_user_by_id(tx["user_id"])
    if not u:
        return res
    tg = u["telegram_id"]
    if res == "ok":
        await drop_qr_message(bot, tx)
        try:
            await bot.send_message(
                tg, f"✅ <b>PAYMENT VERIFIED</b>\n\n💰 {fmt_money(tx['amount'])} added to your wallet!\n"
                    f"🔢 UTR: <code>{esc(utr or 'N/A')}</code>\n\nThank you for choosing <b>{esc(STORE_NAME)}</b>.",
                reply_markup=main_menu(tg), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to notify user {tg}: {e}")
        how = "auto" if not by_admin else f"approved by admin {by_admin}"
        db.log_admin(by_admin or 0, f"{how}: credited tx {tx['id']} ({tx['amount']}) to {tg} utr={utr}")
        if not by_admin:
            await notify_admins(
                bot, f"💳 <b>AUTO PAYMENT</b> #{tx['id']}\n\n👤 {uname(u)}\n🆔 <code>{tg}</code>\n"
                     f"💰 <b>{fmt_money(tx['amount'])}</b>\n🔢 UTR: <code>{esc(utr or 'N/A')}</code>")
    elif res == "duplicate":
        await drop_qr_message(bot, tx)
        try:
            await bot.send_message(
                tg, f"⚠️ This UTR was already used for another payment, so it was not credited.\n"
                    f"Contact {esc(SUPPORT_USERNAME)} if you think this is a mistake.",
                reply_markup=main_menu(tg), parse_mode="HTML")
        except Exception:
            pass
        await notify_admins(bot, f"⚠️ <b>DUPLICATE UTR</b> blocked\n\n👤 {uname(u)} (<code>{tg}</code>)\n"
                                 f"🔢 <code>{esc(utr or '')}</code>  tx #{tx['id']}")
    return res


async def submit_payment(bot, user: Dict, tx_id: Optional[int], utr: str) -> Tuple[str, str]:
    """One engine for the chat and the Mini App. -> (code, plain-text message).
    codes: ok | manual | done | duplicate | fail | used | blocked | closed | error | invalid"""
    uid = user["telegram_id"]
    tx = db.get_transaction(tx_id) if tx_id else None
    if not tx or tx["user_id"] != user["id"]:
        return "closed", "Payment session not found. Please start Add Balance again."
    if tx["status"] == "approved":
        return "done", "This payment was already credited to your wallet."
    if tx["status"] == "review":
        return "manual", "Already submitted. Waiting for admin approval."
    if tx["status"] not in ("pending", "expired"):
        return "closed", "This payment session is closed. Please start Add Balance again."
    if utr_blocked(uid):
        return "blocked", "Too many wrong attempts. Please wait 10 minutes and try again."
    if db.utr_registered(utr):
        _utr_fails.setdefault(uid, []).append(time.time())
        return "used", "This UTR / Transaction ID has already been used."

    if PAY_MODE == "manual":   # admin approves by hand
        if not db.set_tx_review(tx["id"], utr):
            return "closed", "This payment session is closed. Please start Add Balance again."
        await notify_admins(
            bot, f"🕒 <b>MANUAL PAYMENT CHECK</b> #{tx['id']}\n\n👤 {uname(user)}\n🆔 <code>{uid}</code>\n"
                 f"💰 <b>{fmt_money(tx['amount'])}</b>\n🔢 UTR / Txn ID: <code>{esc(utr)}</code>\n\n"
                 f"Check this payment in your bank/UPI app, then choose:",
            kb([[btn("✅ Approve", f"admin_man_ok:{tx['id']}", "success"),
                 btn("❌ Reject", f"admin_man_no:{tx['id']}", "danger")]]))
        return "manual", "Submitted! The admin will check your payment and add the balance soon."

    try:   # automatic: the gateway verifies UTR + amount
        r = await asyncio.to_thread(fmpay_verify, utr, float(tx["amount"]))
    except Exception as e:
        logger.error(f"FMPay verify error: {e}")
        await notify_admins(bot, f"⚠️ <b>Payment API error</b>\n<code>{esc(str(e))[:300]}</code>")
        return "error", "Verification service is not available right now. Please try again in a minute."
    if not r["ok"]:
        _utr_fails.setdefault(uid, []).append(time.time())
        return "fail", (r.get("message") or "")[:150]
    res = await finalize_payment(bot, tx, utr)
    if res == "done":
        return "done", "This payment was already credited to your wallet."
    if res == "invalid":
        return "invalid", f"This payment can't be credited. Contact {SUPPORT_USERNAME}."
    if res == "duplicate":
        return "duplicate", "This UTR was already used for another payment, so it was not credited."
    return "ok", "Payment verified. Balance added."


async def verify_by_utr(context, msg, user: Dict, tx_id: Optional[int], utr: str):
    """User sends a UTR in the chat -> gateway verifies (auto) or the admin is asked (manual)."""
    back = main_menu(user["telegram_id"])
    wait = await msg.reply_text("🔎 Checking your payment... (can take up to a minute)")
    try:
        code, text = await submit_payment(context.bot, user, tx_id, utr)
    finally:
        try:
            await wait.delete()
        except Exception:
            pass
    tx = db.get_transaction(tx_id) if tx_id else None
    if code == "fail":
        reason = f"\n\nReason: {esc(text)}" if text else ""
        amt = fmt_money(tx["amount"]) if tx else "this amount"
        await msg.reply_text(
            f"❌ <b>Payment not verified</b>\n\nWe could not find a payment of {amt} "
            f"with this UTR / Transaction ID.{reason}\n\nCheck the ID and the exact amount, then try again, or contact "
            f"{esc(SUPPORT_USERNAME)}.",
            reply_markup=kb([[btn("🔢 Try Again", f"utr_verify:{tx_id}", "primary")],
                             [btn("⬅️ Main Menu", "back:main")]]),
            parse_mode="HTML")
    elif code == "manual":
        await msg.reply_text(f"🕒 <b>Sent to admin</b>\n\n{esc(text)}", reply_markup=back, parse_mode="HTML")
    elif code in ("ok", "duplicate"):
        return   # finalize_payment already messaged the user
    else:
        await msg.reply_text(("⚠️ " if code == "error" else "❌ ") + esc(text), reply_markup=back, parse_mode="HTML")


async def manual_decision(context, query, admin_id: int, data: str):
    """Admin taps Approve / Reject on a manual payment check."""
    approve = data.startswith("admin_man_ok:")
    tx = db.get_transaction(int(data.split(":", 1)[1]))
    if not tx:
        await edit(query, "❌ Payment not found.")
        return
    base = (query.message.text_html if query.message and query.message.text else "") or ""
    if tx["status"] != "review":
        await edit(query, f"{base}\n\nℹ️ Already handled (status: <b>{esc(tx['status'])}</b>).")
        return
    u = db.get_user_by_id(tx["user_id"])
    if approve:
        res = await finalize_payment(context.bot, tx, tx.get("utr"), by_admin=admin_id)
        label = {"ok": "✅ <b>Approved. Wallet credited.</b>",
                 "done": "ℹ️ Already credited.",
                 "duplicate": "⚠️ <b>Blocked: this UTR was already used.</b>"}.get(res, "❌ Could not credit.")
        await edit(query, f"{base}\n\n{label}")
    else:
        db.set_tx_status(tx["id"], "rejected", allowed=("review",))
        db.log_admin(admin_id, f"rejected manual payment tx {tx['id']} ({tx['amount']})")
        await edit(query, f"{base}\n\n❌ <b>Rejected.</b>")
        if u:
            try:
                await context.bot.send_message(
                    u["telegram_id"],
                    f"❌ <b>Payment not approved</b>\n\nWe could not confirm your payment of {fmt_money(tx['amount'])}.\n"
                    f"Contact {esc(SUPPORT_USERNAME)} with your payment proof.",
                    reply_markup=main_menu(u["telegram_id"]), parse_mode="HTML")
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════
# MINI APP PAYMENT PAGE  (opens inside Telegram from the "Pay Now" button)
# ═══════════════════════════════════════════════════════════════════

WEBAPP_HOST = os.getenv("WEBAPP_HOST", "127.0.0.1")   # reverse proxy / tunnel talks to this
WEB: Dict[str, Any] = {"loop": None, "bot": None, "logo": None}


def pay_sig(tx_id: int) -> str:
    """Signature that proves the link was made by the bot for this exact payment."""
    return hmac.new(BOT_TOKEN.encode(), f"pay:{tx_id}".encode(), hashlib.sha256).hexdigest()[:24]


def pay_page_url(tx_id: int) -> Optional[str]:
    if not WEBAPP_URL.startswith("https://"):
        return None
    return f"{WEBAPP_URL}/pay?tx={tx_id}&s={pay_sig(tx_id)}"


def tx_seconds_left(tx: Dict) -> int:
    try:
        created = datetime.strptime(str(tx["created_at"])[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return max(0, int(PAYMENT_EXPIRY_MIN * 60 - (datetime.now(timezone.utc) - created).total_seconds()))
    except Exception:
        return PAYMENT_EXPIRY_MIN * 60


def qr_data_uri(text: str) -> str:
    bio = io.BytesIO()
    qrcode.make(text, box_size=8, border=1).save(bio, "PNG")
    return "data:image/png;base64," + base64.b64encode(bio.getvalue()).decode()


async def refresh_logo(bot):
    """Downloads the bot's own profile photo once (and every few hours) for the payment page header."""
    data = None
    try:
        me = await bot.get_me()
        try:
            ph = await bot.get_user_profile_photos(me.id, limit=1)
            if ph.total_count and ph.photos:
                f = await bot.get_file(ph.photos[0][-1].file_id)
                data = bytes(await f.download_as_bytearray())
        except Exception as e:
            logger.info(f"logo via profile photos failed: {e}")
        if not data:
            chat = await bot.get_chat(me.id)
            if getattr(chat, "photo", None):
                f = await bot.get_file(chat.photo.big_file_id)
                data = bytes(await f.download_as_bytearray())
    except Exception as e:
        logger.info(f"bot logo not available: {e}")
    if data:
        WEB["logo"] = data
        logger.info(f"Bot logo loaded ({len(data)} bytes)")


async def logo_job(context: ContextTypes.DEFAULT_TYPE):
    await refresh_logo(context.bot)


def pay_page_data(tx: Dict) -> Dict[str, Any]:
    amount = float(tx["amount"])
    ref = tx.get("order_id") or f"TX{tx['id']}"
    q = (f"pa={quote(UPI_ID, safe='@')}&pn={quote(STORE_NAME)}&am={amount:.2f}&cu=INR&tn={quote(str(ref))}")
    return {
        "tx": tx["id"], "s": pay_sig(tx["id"]), "amount": f"{amount:.2f}", "ref": ref,
        "store": STORE_NAME, "upi": UPI_ID, "mode": PAY_MODE, "status": tx["status"],
        "left": tx_seconds_left(tx), "total": PAYMENT_EXPIRY_MIN * 60,
        "support": SUPPORT_USERNAME, "qr": qr_data_uri(f"upi://pay?{q}"), "q": q,
        # pkg = Android app package (used with an intent:// link), ios = iOS URL scheme of the app
        "apps": [
            {"id": "gpay", "name": "Google Pay", "pkg": "com.google.android.apps.nbu.paisa.user", "ios": "gpay://upi/pay"},
            {"id": "phonepe", "name": "PhonePe", "pkg": "com.phonepe.app", "ios": "phonepe://pay"},
            {"id": "paytm", "name": "Paytm", "pkg": "net.one97.paytm", "ios": "paytmmp://pay"},
            {"id": "fampay", "name": "FamPay", "pkg": "com.fampay.in", "ios": "upi://pay"},
            {"id": "bhim", "name": "BHIM", "pkg": "in.org.npci.upiapp", "ios": "upi://pay"},
            {"id": "other", "name": "Any UPI app", "pkg": "", "ios": "upi://pay"},
        ],
    }


PAY_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="theme-color" content="#07090f">
<title>Secure payment</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#07090f;--s1:#0f1320;--s2:#161c2f;--line:#232b43;--tx:#eef1f8;--mu:#8b93ab;--ac:#c6ff3d;--ac2:#9be11a;--amber:#ffb020;--red:#ff5d6c;--blue:#5b8cff}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;color:var(--tx);font-family:Sora,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
body{min-height:100vh;padding:0 16px max(28px,env(safe-area-inset-bottom));
  background:radial-gradient(90% 46% at 100% 0,rgba(91,140,255,.20),transparent 60%),radial-gradient(70% 38% at 0 14%,rgba(198,255,61,.10),transparent 62%),var(--bg)}
button,input{font:inherit;color:inherit}
.top{display:flex;align-items:center;gap:11px;padding:14px 0 4px}
.logo{width:38px;height:38px;border-radius:12px;object-fit:cover;background:var(--s2);flex:none;display:grid;place-items:center;font-weight:800;color:var(--ac);overflow:hidden;border:1px solid var(--line)}
.who{min-width:0;flex:1}
.who b{display:block;font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.who small{display:flex;align-items:center;gap:5px;color:var(--ac);font-size:11.5px;font-weight:600}
.x{width:34px;height:34px;border-radius:11px;border:1px solid var(--line);background:var(--s1);color:var(--mu);font-size:15px;cursor:pointer}
.ring{position:relative;width:232px;height:232px;margin:12px auto 0}
.ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.ring .tr{fill:none;stroke:var(--line);stroke-width:4}
.ring .pg{fill:none;stroke:var(--ac);stroke-width:4;stroke-linecap:round;stroke-dasharray:339.29;stroke-dashoffset:0;transition:stroke-dashoffset 1s linear,stroke .4s;filter:drop-shadow(0 0 5px rgba(198,255,61,.55))}
.ring .pg.warn{stroke:var(--amber);filter:drop-shadow(0 0 5px rgba(255,176,32,.5))}
.ring .pg.out{stroke:var(--red);filter:none}
.mid{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:0 30px}
.mid small{font-size:11px;font-weight:700;letter-spacing:1.6px;color:var(--mu)}
.amt{font-size:42px;font-weight:800;letter-spacing:-1.5px;line-height:1.1;margin:5px 0 8px;font-variant-numeric:tabular-nums}
.amt i{font-style:normal;font-size:24px;color:var(--ac);margin-right:2px;letter-spacing:0}
.refb{border:1px dashed var(--line);background:transparent;border-radius:999px;padding:5px 11px;font-size:11.5px;font-weight:600;color:var(--mu);cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.timer{text-align:center;margin:8px 0 0;font-size:12.5px;font-weight:600;color:var(--mu)}
.timer b{color:var(--tx);font-variant-numeric:tabular-nums;font-size:14px}
.timer.out{color:var(--red)}
.seg{display:grid;grid-template-columns:1fr 1fr;background:var(--s1);border:1px solid var(--line);border-radius:15px;padding:4px;margin:16px 0 12px}
.tab{border:0;background:transparent;border-radius:11px;padding:11px 6px;font-size:13px;font-weight:700;color:var(--mu);cursor:pointer;display:flex;align-items:center;justify-content:center;gap:7px}
.tab em{font-style:normal;width:20px;height:20px;border-radius:50%;background:var(--line);display:grid;place-items:center;font-size:11px}
.tab.on{background:var(--ac);color:#0b1200}.tab.on em{background:#0b1200;color:var(--ac)}
.panel{display:none}.panel.on{display:block;animation:in .28s ease}
@keyframes in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.card{background:var(--s1);border:1px solid var(--line);border-radius:22px;padding:16px}
.qrw{position:relative;width:216px;margin:0 auto;padding:13px;background:#fff;border-radius:18px}
.qrw img{display:block;width:100%;aspect-ratio:1;image-rendering:pixelated}
.qrw s{position:absolute;width:20px;height:20px;border:3px solid var(--ac2);text-decoration:none}
.qrw s:nth-of-type(1){top:5px;left:5px;border-right:0;border-bottom:0;border-radius:8px 0 0 0}
.qrw s:nth-of-type(2){top:5px;right:5px;border-left:0;border-bottom:0;border-radius:0 8px 0 0}
.qrw s:nth-of-type(3){bottom:5px;left:5px;border-right:0;border-top:0;border-radius:0 0 0 8px}
.qrw s:nth-of-type(4){bottom:5px;right:5px;border-left:0;border-top:0;border-radius:0 0 8px 0}
.scan{position:absolute;left:14px;right:14px;height:2px;background:linear-gradient(90deg,transparent,var(--ac2),transparent);box-shadow:0 0 12px var(--ac2);animation:scan 2.6s ease-in-out infinite}
@keyframes scan{0%,100%{top:16px}50%{top:calc(100% - 18px)}}
.cap{text-align:center;color:var(--mu);font-size:12px;font-weight:600;margin:11px 0 2px}
.sep{display:flex;align-items:center;gap:10px;margin:16px 0 12px;color:var(--mu);font-size:11.5px;font-weight:700;letter-spacing:1px}
.sep::before,.sep::after{content:"";height:1px;background:var(--line);flex:1}
.apps{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}
.app{display:flex;flex-direction:column;align-items:center;gap:7px;padding:12px 4px 10px;border:1px solid var(--line);border-radius:16px;background:var(--s2);cursor:pointer}
.app:active{transform:scale(.96);border-color:var(--ac)}
.mk{width:42px;height:42px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:14px;color:#fff}
.app span{font-size:11.5px;font-weight:700;text-align:center;line-height:1.2}
.upi{margin-top:12px;display:flex;align-items:center;gap:10px;background:var(--s2);border:1px solid var(--line);border-radius:14px;padding:10px 12px}
.upi div{flex:1;min-width:0}.upi small{display:block;color:var(--mu);font-size:11px;font-weight:600}
.upi b{display:block;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mini{border:0;background:var(--line);border-radius:10px;padding:8px 12px;font-size:12px;font-weight:700;cursor:pointer}
.lnk{display:block;width:100%;margin-top:10px;border:0;background:transparent;color:var(--blue);font-size:12.5px;font-weight:700;padding:8px;cursor:pointer}
.big{width:100%;border:0;border-radius:15px;padding:15px;background:var(--ac);color:#0b1200;font-weight:800;font-size:15px;cursor:pointer;margin-top:12px}
.big:disabled{opacity:.55}
.big:active{transform:scale(.98)}
h3{margin:0 0 4px;font-size:16px;font-weight:800}
.sub{margin:0 0 14px;color:var(--mu);font-size:12.5px;font-weight:500;line-height:1.5}
.sum{display:flex;justify-content:space-between;align-items:center;background:var(--s2);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin-bottom:12px;font-size:13px;font-weight:600;color:var(--mu)}
.sum b{color:var(--ac);font-size:16px;font-variant-numeric:tabular-nums}
.inp{width:100%;border:1.5px solid var(--line);background:var(--s2);border-radius:14px;padding:15px 14px;font-size:15.5px;font-weight:600;outline:0;letter-spacing:.3px}
.inp:focus{border-color:var(--ac);box-shadow:0 0 0 3px rgba(198,255,61,.14)}
.inp::placeholder{color:#59627d;font-weight:500}
.msg{margin-top:12px;border-radius:13px;padding:12px 13px;font-size:13px;font-weight:600;line-height:1.45;display:none}
.msg.err{display:block;background:rgba(255,93,108,.12);color:#ff9aa4;border:1px solid rgba(255,93,108,.3)}
.msg.wait{display:flex;gap:10px;align-items:center;background:rgba(255,176,32,.12);color:#ffcf70;border:1px solid rgba(255,176,32,.3)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--amber);flex:none;animation:pu 1.4s ease-in-out infinite}
@keyframes pu{50%{transform:scale(1.5);opacity:.5}}
.hint{margin:12px 2px 0;color:var(--mu);font-size:12px;line-height:1.55;font-weight:500}
.foot{margin:18px 0 0;text-align:center;color:var(--mu);font-size:12px;font-weight:600}
.foot a{color:var(--ac);text-decoration:none;font-weight:700}
.toast{position:fixed;left:50%;bottom:calc(24px + env(safe-area-inset-bottom));transform:translate(-50%,20px);background:#fff;color:#0b1200;padding:10px 16px;border-radius:12px;font-size:12.5px;font-weight:700;opacity:0;pointer-events:none;transition:.25s;max-width:88vw;text-align:center;z-index:20}
.toast.on{opacity:1;transform:translate(-50%,0)}
.done{position:fixed;inset:0;background:var(--bg);display:none;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:30px;z-index:30}
.done.on{display:flex}
.done svg{width:104px;height:104px}
.done circle{fill:rgba(198,255,61,.08);stroke:var(--ac);stroke-width:4;stroke-dasharray:289;stroke-dashoffset:289;animation:dr .7s .05s ease-out forwards}
.done path{fill:none;stroke:var(--ac);stroke-width:6;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:60;stroke-dashoffset:60;animation:dr .45s .6s ease-out forwards}
@keyframes dr{to{stroke-dashoffset:0}}
.done h2{margin:22px 0 6px;font-size:25px;font-weight:800}
.done p{margin:0;color:var(--mu);font-size:14.5px;font-weight:600;line-height:1.55}
.done .big{width:auto;padding:15px 38px;margin-top:30px}
button:focus-visible{outline:3px solid #7f8cff;outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.done circle,.done path{stroke-dashoffset:0}}
@media (max-width:350px){.ring{width:200px;height:200px}.amt{font-size:35px}.qrw{width:190px}}
</style>
</head>
<body>
<div class="top">
  <img class="logo" id="logo" alt="" src="/logo">
  <div class="who"><b id="store">Store</b><small><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/></svg>Secure checkout</small></div>
  <button class="x" id="closeBtn" aria-label="Close">✕</button>
</div>

<div class="ring">
  <svg viewBox="0 0 120 120"><circle class="tr" cx="60" cy="60" r="54"/><circle class="pg" id="pg" cx="60" cy="60" r="54"/></svg>
  <div class="mid">
    <small>PAY EXACTLY</small>
    <div class="amt"><i>₹</i><span id="amt">0.00</span></div>
    <button class="refb" id="refBtn" title="Copy reference"># <span id="ref"></span></button>
  </div>
</div>
<div class="timer" id="timer">Session ends in <b id="clock">--:--</b></div>

<div class="seg">
  <button class="tab on" id="t1"><em>1</em>Pay</button>
  <button class="tab" id="t2"><em>2</em>Confirm</button>
</div>

<section class="panel on" id="p1">
  <div class="card">
    <div class="qrw"><img id="qr" alt="UPI QR code"><s></s><s></s><s></s><s></s><div class="scan"></div></div>
    <div class="cap">Scan with any UPI app</div>
    <div class="sep">OR OPEN AN APP</div>
    <div class="apps" id="apps"></div>
    <div class="upi"><div><small>UPI ID</small><b id="upi"></b></div><button class="mini" id="copyUpi">Copy</button></div>
    <button class="lnk" id="browser">App did not open? Open this page in your browser</button>
  </div>
  <button class="big" id="next">I have paid  →</button>
</section>

<section class="panel" id="p2">
  <div class="card">
    <h3>Confirm your payment</h3>
    <p class="sub" id="sub">Enter the UTR / transaction ID from your payment app.</p>
    <div class="sum"><span>Amount to confirm</span><b id="amt2"></b></div>
    <input class="inp" id="utr" inputmode="text" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="UTR / Transaction ID" aria-label="UTR or transaction ID">
    <div class="msg" id="msg"></div>
    <button class="big" id="go">Verify payment</button>
    <p class="hint" id="hint">GPay, PhonePe, Paytm: the 12-digit UTR in payment details. FamPay: the transaction ID.</p>
  </div>
</section>

<p class="foot">Pay the exact amount shown. Need help? <a id="support" href="#">Contact support</a></p>

<div class="done" id="done" role="status">
  <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="46"/><path d="M30 52l14 14 27-30"/></svg>
  <h2>Payment received</h2>
  <p id="doneText">Your wallet balance is updated.</p>
  <button class="big" id="doneBtn">Back to bot</button>
</div>
<div class="toast" id="toast"></div>

<script>
const D = __DATA__;
const tg = window.Telegram && window.Telegram.WebApp;
try { if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor("#07090f"); tg.setBackgroundColor("#07090f"); } } catch (e) {}
const $ = id => document.getElementById(id);
const MARKS = {gpay:["#1a73e8","G"], phonepe:["#5f259f","Pe"], paytm:["#0a9fd8","Pt"], fampay:["#ffb020","F"], bhim:["#ef6c00","B"], other:["#2f3a5c","UPI"]};
const C = 2 * Math.PI * 54;
let left = D.left, finished = false, busy = false, leftApp = false;

$("store").textContent = D.store;
const money = Number(D.amount).toLocaleString("en-IN", {minimumFractionDigits:2, maximumFractionDigits:2});
$("amt").textContent = money; $("amt2").textContent = "₹" + money;
$("ref").textContent = D.ref; $("upi").textContent = D.upi; $("qr").src = D.qr;
$("support").href = "https://t.me/" + D.support.replace("@", "");
$("logo").onerror = function () {
  const d = document.createElement("div"); d.className = "logo"; d.textContent = (D.store || "S").trim().charAt(0).toUpperCase(); this.replaceWith(d);
};
if (D.mode === "manual") {
  $("go").textContent = "Submit for approval";
  $("sub").textContent = "Enter your UTR / transaction ID. The admin will check it and add your balance.";
}

function haptic(t) { try { tg && tg.HapticFeedback && (t === "ok" ? tg.HapticFeedback.notificationOccurred("success") : tg.HapticFeedback.impactOccurred(t)); } catch (e) {} }
let tt; function toast(s) { const el = $("toast"); el.textContent = s; el.classList.add("on"); clearTimeout(tt); tt = setTimeout(() => el.classList.remove("on"), 2800); }
function copy(text, label) {
  const done = () => { haptic("light"); toast(label + " copied"); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fb); else fb();
  function fb() { const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); done(); } catch (e) {} t.remove(); }
}
$("copyUpi").onclick = () => copy(D.upi, "UPI ID");
$("refBtn").onclick = () => copy(D.ref, "Reference");
$("closeBtn").onclick = $("doneBtn").onclick = () => { if (tg) tg.close(); else history.back(); };
$("browser").onclick = () => { const u = location.href; if (tg && tg.openLink) tg.openLink(u, {try_browser: true}); else window.open(u, "_blank"); };

function setTab(n) {
  $("t1").classList.toggle("on", n === 1); $("t2").classList.toggle("on", n === 2);
  $("p1").classList.toggle("on", n === 1); $("p2").classList.toggle("on", n === 2);
  if (n === 2) setTimeout(() => { try { $("utr").focus({preventScroll: true}); } catch (e) {} }, 300);
}
$("t1").onclick = () => setTab(1); $("t2").onclick = () => setTab(2); $("next").onclick = () => { haptic("light"); setTab(2); };

// UPI app tiles: Android -> intent:// with the app package, iOS -> the app's URL scheme, otherwise upi://
function openApp(a) {
  haptic("light");
  const ua = navigator.userAgent || "";
  let url;
  if (/android/i.test(ua) && a.pkg) url = "intent://pay?" + D.q + "#Intent;scheme=upi;package=" + a.pkg + ";end";
  else if (/iphone|ipad|ipod/i.test(ua)) url = a.ios + "?" + D.q;
  else url = "upi://pay?" + D.q;
  const l = document.createElement("a"); l.href = url; l.rel = "noopener"; document.body.appendChild(l); l.click(); l.remove();
  setTimeout(() => { if (!document.hidden) toast("App did not open? Scan the QR, copy the UPI ID or open in browser."); }, 1800);
}
D.apps.forEach(a => {
  const b = document.createElement("button"); b.className = "app";
  const m = MARKS[a.id] || ["#2f3a5c", "U"];
  b.innerHTML = '<div class="mk" style="background:' + m[0] + '">' + m[1] + '</div><span></span>';
  b.querySelector("span").textContent = a.name;
  b.onclick = () => openApp(a);
  $("apps").appendChild(b);
});
// coming back from a UPI app -> jump to the confirm step
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { leftApp = true; return; }
  if (leftApp && !finished) { leftApp = false; setTab(2); toast("Paid? Enter your UTR to confirm"); }
});
try { tg && tg.onEvent && tg.onEvent("activated", () => { if (leftApp && !finished) { leftApp = false; setTab(2); toast("Paid? Enter your UTR to confirm"); } }); } catch (e) {}

function tick() {
  const m = Math.floor(left / 60), s = left % 60;
  $("clock").textContent = String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  const pg = $("pg"); pg.style.strokeDashoffset = String(C * (1 - Math.max(0, left / D.total)));
  pg.setAttribute("class", "pg" + (left === 0 ? " out" : left < 120 ? " warn" : ""));
  const t = $("timer");
  if (left === 0) { t.className = "timer out"; t.textContent = "Session ended. If you already paid, confirm it in step 2."; }
  else left--;
}
tick(); setInterval(() => { if (left > 0) tick(); }, 1000);

function showMsg(kind, text) {
  const el = $("msg"); el.className = "msg " + kind;
  el.innerHTML = kind === "wait" ? '<i class="dot"></i><span></span>' : "<span></span>";
  el.querySelector("span").textContent = text;
}
function success(text) {
  if (finished) return; finished = true; haptic("ok");
  if (text) $("doneText").textContent = text;
  $("done").classList.add("on");
}
function lock() { $("go").disabled = true; $("utr").disabled = true; }
function applyStatus(s) {
  if (s === "approved") success("₹" + money + " is now in your wallet.");
  else if (s === "review") { setTab(2); showMsg("wait", "Waiting for the admin to approve your payment. You can close this page."); lock(); }
  else if (s === "rejected") { setTab(2); showMsg("err", "The admin could not confirm this payment. Contact support with your payment proof."); lock(); }
}
applyStatus(D.status);

function validId(v) { return /^\d{12}$/.test(v) || (/^[A-Za-z0-9_\-]{8,40}$/.test(v) && /[A-Za-z]/.test(v) && /\d/.test(v)); }
const goLabel = D.mode === "manual" ? "Submit for approval" : "Verify payment";
$("go").onclick = async () => {
  if (busy || finished) return;
  const v = $("utr").value.replace(/\s+/g, "");
  if (!validId(v)) { showMsg("err", "Enter the 12-digit UTR, or the FamPay transaction ID."); haptic("light"); return; }
  busy = true; $("go").disabled = true; $("go").textContent = "Checking…"; $("msg").className = "msg";
  try {
    const r = await fetch("/api/verify", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({tx: D.tx, s: D.s, utr: v})});
    const j = await r.json();
    if (j.code === "ok" || j.code === "done") success("₹" + money + " is now in your wallet.");
    else if (j.code === "manual") applyStatus("review");
    else showMsg("err", j.message || "Could not verify this payment. Check the ID and the exact amount.");
  } catch (e) { showMsg("err", "No connection. Check your internet and try again."); }
  busy = false;
  if (!finished && !$("utr").disabled) $("go").disabled = false;
  $("go").textContent = goLabel;
};
$("utr").addEventListener("keydown", e => { if (e.key === "Enter") $("go").click(); });

async function poll() {
  if (finished) return;
  try { const r = await fetch("/api/status?tx=" + D.tx + "&s=" + D.s, {cache: "no-store"}); const j = await r.json(); applyStatus(j.status); } catch (e) {}
}
setInterval(poll, 3500);
</script>
</body>
</html>
"""


class PayHandler(BaseHTTPRequestHandler):
    server_version = "StorePay/1.0"

    def log_message(self, *args):   # keep the bot log clean
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8", cache: str = "no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Dict):
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    @staticmethod
    def _tx(tx_raw, sig) -> Optional[Dict]:
        try:
            tx_id = int(tx_raw)
        except (TypeError, ValueError):
            return None
        if not hmac.compare_digest(str(sig or ""), pay_sig(tx_id)):
            return None
        return db.get_transaction(tx_id)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            if u.path == "/pay":
                tx = self._tx(qs.get("tx", [""])[0], qs.get("s", [""])[0])
                if not tx:
                    self._send(403, b"This payment link is not valid. Please open it again from the bot.", "text/plain; charset=utf-8")
                    return
                data = json.dumps(pay_page_data(tx)).replace("</", "<\\/")
                self._send(200, PAY_PAGE_HTML.replace("__DATA__", data).encode())
            elif u.path == "/api/status":
                tx = self._tx(qs.get("tx", [""])[0], qs.get("s", [""])[0])
                if not tx:
                    self._json(403, {"status": "invalid"})
                    return
                self._json(200, {"status": tx["status"], "left": tx_seconds_left(tx)})
            elif u.path == "/logo":
                if WEB["logo"]:
                    self._send(200, WEB["logo"], "image/jpeg", "public, max-age=3600")
                else:
                    self._send(404, b"", "text/plain")
            elif u.path == "/health":
                self._send(200, b"ok", "text/plain")
            else:
                self._send(404, b"Not found", "text/plain")
        except Exception as e:
            logger.error(f"web GET error: {e}")
            try:
                self._send(500, b"Server error", "text/plain")
            except Exception:
                pass

    def do_POST(self):
        try:
            if urlparse(self.path).path != "/api/verify":
                self._send(404, b"Not found", "text/plain")
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 4000:
                self._json(400, {"code": "invalid", "message": "Bad request."})
                return
            body = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
            tx = self._tx(body.get("tx"), body.get("s"))
            if not tx:
                self._json(403, {"code": "closed", "message": "This payment link is not valid. Open it again from the bot."})
                return
            utr = norm_txn_id(str(body.get("utr", "")))
            if not utr:
                self._json(200, {"code": "invalid", "message": "Enter the 12-digit UTR, or the FamPay transaction ID."})
                return
            user = db.get_user_by_id(tx["user_id"])
            if not user or not WEB["loop"]:
                self._json(503, {"code": "error", "message": "The bot is starting. Try again in a moment."})
                return
            fut = asyncio.run_coroutine_threadsafe(submit_payment(WEB["bot"], user, tx["id"], utr), WEB["loop"])
            code, text = fut.result(timeout=110)
            if code == "fail":
                text = ("Payment not found. Check the ID and the exact amount." +
                        (f" ({text})" if text else ""))
            self._json(200, {"code": code, "message": text})
        except Exception as e:
            logger.error(f"web POST error: {e}")
            try:
                self._json(500, {"code": "error", "message": "Something went wrong. Please try again."})
            except Exception:
                pass


def start_web_server():
    try:
        srv = ThreadingHTTPServer((WEBAPP_HOST, WEBAPP_PORT), PayHandler)
    except OSError as e:
        logger.warning(f"Pay page server could not start on {WEBAPP_HOST}:{WEBAPP_PORT}: {e}")
        return
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logger.info(f"Pay page server on {WEBAPP_HOST}:{WEBAPP_PORT} | public URL: {WEBAPP_URL or 'NOT SET (QR photo is used)'}")


async def post_init(app):
    WEB["loop"] = asyncio.get_running_loop()
    WEB["bot"] = app.bot
    start_web_server()


# ═══════════════════════════════════════════════════════════════════
# BACKGROUND JOBS / ERRORS / MAIN
# ═══════════════════════════════════════════════════════════════════

async def expire_job(context: ContextTypes.DEFAULT_TYPE):
    n = db.expire_stale(PAYMENT_EXPIRY_MIN)
    if n:
        logger.info(f"Expired {n} unfinished payment(s)")


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error", exc_info=context.error)


def main():
    apply_settings()   # UPI ID / store name / etc. changed from the admin panel
    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN missing. Paste it in BOT_TOKEN_VALUE at the top of storebot.py (or .env).")
    if not ADMIN_IDS:
        sys.exit("ADMIN ID missing. Set ADMIN_ID_VALUE at the top of storebot.py (or ADMIN_IDS in .env).")
    if not FMPAY_API_KEY:
        logger.warning("FMPay API key missing - set it in Admin Panel -> Settings (or use MANUAL payment mode).")
    if not UPI_ID:
        sys.exit("UPI ID missing. Set UPI_ID_VALUE at the top of storebot.py.")
    if not API_HEADERS["X-API-Key"] or not API_HEADERS["M-Key"]:
        logger.warning("API_KEY / API_MKEY missing in .env - key delivery will fail!")

    bot = BoldBot(token=BOT_TOKEN, defaults=Defaults(parse_mode="HTML"))
    app = Application.builder().bot(bot).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.VIDEO | filters.Document.IMAGE,
                                   media_handler))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(expire_job, interval=300, first=60)
        app.job_queue.run_repeating(remind_job, interval=60, first=45)
        app.job_queue.run_repeating(logo_job, interval=6 * 3600, first=3)
    else:
        logger.warning("job-queue not installed: pip install 'python-telegram-bot[job-queue]'")

    logger.info(f"🚀 {STORE_NAME} Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
