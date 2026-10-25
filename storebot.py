#!/usr/bin/env python3
"""
SANDY STORE - TELEGRAM BOT  (v4 - FMPay auto payment verification)

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
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from urllib.parse import quote

import requests

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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
FMPAY_BASE_URL_VALUE = "https://famgateway.in"                          # ← FMPay API base URL
# ────────────────────────────────────────────────────────────────

BOT_TOKEN = BOT_TOKEN_VALUE.strip() or os.getenv("BOT_TOKEN", "").strip()
_admin_raw = ADMIN_ID_VALUE.strip() or os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.isdigit()}
FMPAY_API_KEY = FMPAY_API_KEY_VALUE.strip() or os.getenv("FMPAY_API_KEY", "").strip()
FMPAY_BASE_URL = (FMPAY_BASE_URL_VALUE.strip() or os.getenv("FMPAY_BASE_URL", "")).rstrip("/")
FMPAY_TIMEOUT = 15
PAYMENT_POLL_SEC = int(os.getenv("PAYMENT_POLL_SEC", "5"))   # how often pending payments are checked
STORE_NAME = os.getenv("STORE_NAME", "SANDY_STORE01")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@SANDY_STORE01")

API_URL = os.getenv("API_URL", "https://fflevel.in/api/external/panel/keygen")
API_HEADERS = {
    "X-API-Key": os.getenv("API_KEY", ""),
    "M-Key": os.getenv("API_MKEY", ""),
}

MIN_RECHARGE = float(os.getenv("MIN_RECHARGE", "10"))
MAX_RECHARGE = float(os.getenv("MAX_RECHARGE", "10000"))
REFERRAL_PERCENT = float(os.getenv("REFERRAL_PERCENT", "2"))
PAYMENT_EXPIRY_MIN = int(os.getenv("PAYMENT_EXPIRY_MIN", "15"))
TZ_OFFSET_MIN = int(os.getenv("TZ_OFFSET_MIN", "330"))  # IST = +5:30

DB_NAME = os.path.join(BASE_DIR, os.getenv("DB_NAME", "SHADMAN_store.db"))

API_HOURS_MAP = {
    "1 Hour": 1, "3 Hours": 3, "6 Hours": 6, "12 Hours": 12, "1 Day": 24,
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# FMPAY CLIENT  (auto UPI payment + UTR verification)
# ═══════════════════════════════════════════════════════════════════

class FmPayError(Exception):
    pass


def _fm_headers() -> Dict[str, str]:
    return {"X-Api-Key": FMPAY_API_KEY, "Accept": "application/json"}


def _fm_json(resp) -> Dict[str, Any]:
    try:
        j = resp.json()
    except ValueError:
        raise FmPayError(f"Gateway returned a non-JSON reply (HTTP {resp.status_code})")
    if not isinstance(j, dict):
        raise FmPayError("Unexpected gateway reply")
    return j


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmpay_create_order(amount: float, customer: str) -> Dict[str, Any]:
    """Creates a payment order -> order_id, qr_url, checkout_url, upi_intent, payable_amount ..."""
    resp = requests.post(
        f"{FMPAY_BASE_URL}/api/create-order",
        json={"amount": round(float(amount), 2), "customer_name": customer[:60]},
        headers=_fm_headers(), timeout=FMPAY_TIMEOUT)
    j = _fm_json(resp)
    data = j.get("data")
    if resp.status_code != 200 or j.get("status") != "success" or not isinstance(data, dict) \
            or not data.get("order_id"):
        raise FmPayError(str(j.get("message") or f"HTTP {resp.status_code}"))
    return data


def fmpay_get_status(order_id: str) -> Dict[str, Any]:
    """-> {'status': 'success' | 'pending' | 'expired', 'utr', 'amount', 'sender'}"""
    resp = requests.get(f"{FMPAY_BASE_URL}/api/verify-order.php", params={"order_id": order_id},
                        headers=_fm_headers(), timeout=FMPAY_TIMEOUT)
    j = _fm_json(resp)
    status = str(j.get("status") or "").lower()
    data = j.get("data") if isinstance(j.get("data"), dict) else j
    if status == "success":
        return {"status": "success",
                "utr": str(data.get("utr") or "").strip() or None,
                "amount": _to_float(data.get("amount")),
                "sender": data.get("sender_name")}
    if status == "expired" or resp.status_code == 408:
        return {"status": "expired"}
    if status == "pending":
        return {"status": "pending"}
    raise FmPayError(str(j.get("message") or f"HTTP {resp.status_code}"))


def _find_records(obj) -> List[Dict]:
    """Finds the list of transaction dicts inside whatever JSON shape the API returns."""
    if isinstance(obj, list):
        return obj if obj and all(isinstance(x, dict) for x in obj) else []
    if isinstance(obj, dict):
        for v in obj.values():
            found = _find_records(v)
            if found:
                return found
    return []


def fmpay_find_by_utr(utr: str) -> Optional[Dict[str, Any]]:
    """Looks a UTR up in the merchant's successful payments (last 7 days)."""
    for page in range(1, 5):
        resp = requests.get(
            f"{FMPAY_BASE_URL}/api/get-transactions.php",
            params={"filter": "success", "date": "7days", "limit": 25, "page": page},
            headers=_fm_headers(), timeout=FMPAY_TIMEOUT)
        j = _fm_json(resp)
        if resp.status_code != 200:
            raise FmPayError(str(j.get("message") or f"HTTP {resp.status_code}"))
        records = _find_records(j)
        for rec in records:
            if str(rec.get("utr") or rec.get("UTR") or "").strip() == utr:
                return rec
        if len(records) < 25:
            break
    return None


def fmpay_download(url: Optional[str]) -> Optional[bytes]:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and r.content:
            return r.content
    except requests.RequestException:
        pass
    return None


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
                             ("chat_id", "BIGINT"), ("message_id", "INTEGER")):
                if col not in tcols:
                    conn.execute(f"ALTER TABLE transactions ADD COLUMN {col} {ddl}")
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

    def create_product(self, name, group, description, use_api=1, api_version="v2") -> int:
        return self._run(
            "INSERT INTO products (name, group_name, description, use_api, api_version) VALUES (?, ?, ?, ?, ?)",
            (name, group, description, use_api, api_version), "id")

    def add_product_plan(self, product_id, plan_name, price, api_hours=None):
        self._run("INSERT INTO product_plans (product_id, plan_name, price, api_hours) VALUES (?, ?, ?, ?)",
                  (product_id, plan_name, price, api_hours))

    def update_plan_price(self, plan_id: int, price: float):
        self._run("UPDATE product_plans SET price = ? WHERE id = ?", (price, plan_id))

    def set_product_status(self, product_id: int, status: str):
        self._run("UPDATE products SET status = ? WHERE id = ?", (status, product_id))

    def delete_product(self, product_id: int):
        self._run("DELETE FROM product_plans WHERE product_id = ?", (product_id,))
        self._run("DELETE FROM products WHERE id = ?", (product_id,))

    def get_product_groups(self) -> List[str]:
        rows = self._run("SELECT DISTINCT group_name FROM products WHERE status = 'active'", fetch="all")
        return [r["group_name"] for r in rows]

    # ─── orders ───
    def create_order(self, order_id, user_id, product_id, plan_id, plan_name, price,
                     key_code=None, android_id=None, api_response=None):
        self._run(
            "INSERT INTO orders (order_id, user_id, product_id, plan_id, plan_name, price, key_code, android_id, api_response) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, user_id, product_id, plan_id, plan_name, price, key_code, android_id, api_response))

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
        return self._run("SELECT id FROM transactions WHERE utr = ? AND status = 'approved'",
                         (utr,), "one") is not None

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
            if tx["status"] not in ("pending", "expired"):
                conn.rollback()
                return "invalid"
            if utr and conn.execute("SELECT 1 FROM transactions WHERE utr = ? AND status = 'approved'",
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
    if plan_name in API_HOURS_MAP:
        return API_HOURS_MAP[plan_name]
    m = re.search(r"(\d+)\s*(hours?|hrs?|h|days?|d)\b", plan_name.lower())
    if not m:
        return None
    n = int(m.group(1))
    return n * 24 if m.group(2).startswith("d") else n


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

    async def send_message(self, chat_id, text, *args, **kwargs):
        return await super().send_message(chat_id, self._fix(text), *args, **kwargs)

    async def edit_message_text(self, text, *args, **kwargs):
        return await super().edit_message_text(self._fix(text), *args, **kwargs)

    async def send_photo(self, chat_id, photo, caption=None, *args, **kwargs):
        return await super().send_photo(chat_id, photo, self._fix(caption), *args, **kwargs)

    async def send_document(self, chat_id, document, caption=None, *args, **kwargs):
        return await super().send_document(chat_id, document, self._fix(caption), *args, **kwargs)

    async def edit_message_caption(self, *args, caption=None, **kwargs):
        return await super().edit_message_caption(*args, caption=self._fix(caption), **kwargs)


# ─── colourful bold buttons (Bot API 9.4 `style`) ───
def btn(text: str, cb: Optional[str] = None, style: Optional[str] = "primary", url: Optional[str] = None):
    """Every button is coloured + bold. style: 'primary' blue | 'success' green | 'danger' red.
    Falls back safely (no colour) on old library versions."""
    label = bold(text)
    kw: Dict[str, Any] = {}
    if cb:
        kw["callback_data"] = cb
    if url:
        kw["url"] = url
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
    rows = [
        [btn("🛒 Store", "store", "primary"), btn("💰 Wallet", "wallet", "success")],
        [btn("📦 My Orders", "orders", "primary"), btn("👤 Profile", "profile", "primary")],
        [btn("🎁 Redeem Coupon", "redeem_coupon", "success"), btn("🏆 Leaderboard", "leaderboard", "primary")],
        [btn("📖 Help", "help", "success"), btn("📞 Support", "support", "primary")],
    ]
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
        return f"🚫 <b>You are banned.</b>\n\nContact {esc(SUPPORT_USERNAME)}"
    if db.get_setting("maintenance") == "1":
        return ("🔧 <b>Bot Under Maintenance</b>\n\nWe will be back very soon.\n"
                f"Support: {esc(SUPPORT_USERNAME)}")
    return None


def help_text() -> str:
    return (
        head("📖", "Help Guide") +
        "🛒 Store - Browse and buy gaming services\n"
        "💰 Wallet - Add balance via UPI\n"
        "📦 Orders - Your purchases and keys\n"
        "👤 Profile - Stats and referral link\n"
        "🎁 Coupons - Redeem discount codes\n"
        "🏆 Leaderboard - Top buyers and referrers\n\n"
        f"{DIV}\n💳 <b>How to add balance?</b>\n"
        "1. Wallet → Add Balance\n2. Select amount\n3. Scan the QR / pay the exact amount shown\n"
        "4. Balance is added automatically within seconds ✅\n\n"
        f"📞 Support: {esc(SUPPORT_USERNAME)}"
    )


async def edit(query, text: str, markup=None):
    """Edit the message, whether it is text or a photo. Falls back to delete + resend."""
    try:
        if query.message and query.message.photo:
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
        await update.message.reply_text(blocked)
        return
    context.user_data.clear()
    await update.message.reply_text(welcome_text(user), reply_markup=main_menu(tg.id))


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
    if not data.startswith("verify_pay:"):   # that one answers with its own alert
        await query.answer()

    if not is_admin(uid) and flooded(uid, 0.4):
        return

    user = ensure_user(update.effective_user)
    blocked = gate_text(user, uid)
    if blocked:
        await edit(query, blocked)
        return
    if data.startswith(ADMIN_PREFIXES) and not is_admin(uid):
        return

    context.user_data.pop("state", None)
    bot_username = (await context.bot.get_me()).username

    # ─── User navigation ───
    if data == "store":
        await show_store(query)
    elif data.startswith("store_cat:"):
        await show_store_category(query, data[len("store_cat:"):])
    elif data.startswith("prod:"):
        await show_product(query, int(data.split(":")[1]))
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
    elif data.startswith("verify_pay:"):
        await manual_verify_payment(query, context, user, int(data.split(":")[1]))
    elif data.startswith("utr_verify:"):
        context.user_data["state"] = "await_utr"
        context.user_data["utr_tx_id"] = int(data.split(":")[1])
        await context.bot.send_message(
            query.message.chat_id,
            "🔢 <b>Verify via UTR</b>\n\nSend the 12-digit UTR / reference number of your payment:",
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
    elif data == "leaderboard":
        await show_leaderboard(query)
    elif data == "redeem_coupon":
        context.user_data["state"] = "await_coupon"
        await edit(query, "🎁 <b>Enter coupon code:</b>", kb([cancel_row()]))
    elif data == "referral":
        await show_referral(query, user, bot_username)
    elif data.startswith("back:"):
        dest = data.split(":")[1]
        if dest == "main":
            await edit(query, welcome_text(user), main_menu(uid))
        elif dest == "store":
            await show_store(query)
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
        await edit(query, "➕ <b>Add New Product</b>\n\nStep 1/4: Enter product name", kb([cancel_row()]))
    elif data.startswith("prod_group:"):
        context.user_data["prod_group"] = data[len("prod_group:"):]
        context.user_data["state"] = "await_prod_plans"
        await edit(query,
                   f"<b>Step 3/4:</b> Enter plans for <b>{esc(context.user_data.get('prod_name', ''))}</b>\n\n"
                   f"One per line, format <code>Name = Price</code>\n"
                   f"<code>1 Hour = 25</code>\n<code>3 Hours = 55</code>\n<code>1 Day = 290</code>\n\n"
                   f"Plan name must contain hours/days (e.g. <code>2 Days</code>) so the API knows the duration.",
                   kb([cancel_row()]))
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
        plan = db.get_plan(int(data.split(":")[1]))
        if plan:
            context.user_data["state"] = "await_plan_price"
            context.user_data["edit_plan_id"] = plan["id"]
            await edit(query,
                       f"✏️ <b>{esc(plan['plan_name'])}</b>\nCurrent price: {fmt_money(plan['price'])}\n\n"
                       f"Send the new price:", kb([cancel_row()]))
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
        await edit(query, "📢 <b>Broadcast</b>\n\nSend the message for all users (bold/italic formatting works):",
                   kb([cancel_row()]))
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


# ═══════════════════════════════════════════════════════════════════
# STORE
# ═══════════════════════════════════════════════════════════════════

async def show_store(query):
    groups = db.get_product_groups()
    if not groups:
        await edit(query, "🛒 Store is currently empty. Check back later!", back_btn("main"))
        return
    rows = [[btn(g, f"store_cat:{g}", "primary")] for g in groups]
    rows.append([btn("⬅️ Back", "back:main")])
    await edit(query, f"🛒 <b>{esc(STORE_NAME)}</b>\n\nSelect a category 👇", kb(rows))


async def show_store_category(query, group: str):
    products = db.get_products(group)
    if not products:
        await edit(query, "No products in this category.", back_btn("store"))
        return
    rows = [[btn(f"📦 {p['name']}", f"prod:{p['id']}")] for p in products]
    rows.append([btn("⬅️ Back", "back:store")])
    await edit(query, f"<b>{esc(group)}</b>\n\nSelect a product 👇", kb(rows))


async def show_product(query, product_id: int):
    product = db.get_product(product_id)
    if not product or product["status"] != "active":
        await edit(query, "❌ Product not available.", back_btn("store"))
        return
    plans = db.get_product_plans(product_id)
    if not plans:
        await edit(query, "No plans available.", back_btn("store"))
        return
    text = (f"📦 <b>{esc(product['name'])}</b>\n\n{esc(product['description'] or '')}\n\n"
            f"<b>Available Plans:</b>")
    rows = [[btn(f"🛒 {p['plan_name']} - {fmt_money(p['price'])}", f"buy:{product_id}:{p['id']}", "success")]
            for p in plans]
    rows.append([btn("⬅️ Back", "back:store")])
    await edit(query, text, kb(rows))


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
            await edit(query, "❌ Product/Plan not available.", back_btn("store"))
            return
        if not product["use_api"]:
            await edit(query, f"❌ This product is not set up for auto-delivery. Contact {esc(SUPPORT_USERNAME)}.",
                       back_btn("store"))
            return

        api_hours = plan.get("api_hours") or guess_hours(plan["plan_name"])
        if not api_hours:
            await edit(query, f"❌ This plan is not configured correctly. Contact {esc(SUPPORT_USERNAME)}.",
                       back_btn("store"))
            await notify_admins(context.bot, f"⚠️ Plan <b>{esc(plan['plan_name'])}</b> of "
                                              f"<b>{esc(product['name'])}</b> has no valid duration (hours).")
            return
        if product["api_version"] == "v1" and not android_id:
            await edit(query, "❌ Android ID missing. Please start the purchase again.", back_btn("store"))
            return

        price, discount, code = price_with_coupon(user, float(plan["price"]))

        if not db.deduct_balance(tg_id, price):
            await edit(query, "❌ <b>Insufficient balance.</b>",
                       kb([[btn("💳 Add Balance", "add_balance", "success")], [btn("⬅️ Back", "back:store")]]))
            return

        await edit(query, "⏳ <b>Processing your order...</b> Please wait.")

        # ── get the key; refund only if THIS part fails ──
        key_code = None
        api_data: Dict[str, Any] = {}
        error = None
        try:
            api_data = await generate_key_via_api(api_hours, product.get("api_version", "v2"), android_id)
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
                       back_btn("store"))
            await notify_admins(context.bot,
                                f"⚠️ <b>ORDER FAILED</b> (refunded)\n\nUser: {uname(user)} (<code>{tg_id}</code>)\n"
                                f"Product: {esc(product['name'])} / {esc(plan['plan_name'])}\n"
                                f"Error: <code>{esc(error)}</code>")
            return

        # ── key exists: from here we NEVER refund ──
        order_id = generate_order_id()
        try:
            db.create_order(order_id, user["id"], product_id, plan_id, plan["plan_name"], price,
                            key_code, android_id, json.dumps(api_data))
            db.increment_orders(tg_id)
            if code:
                db.set_active_coupon(tg_id, None)
        except Exception as e:
            logger.error(f"Order save failed (key was delivered): {e}")

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
                f"🆔 Order ID: <code>#{order_id}</code>\n\n🔑 <b>YOUR KEY:</b>\n<code>{esc(key_code)}</code>\n\n"
                f"📞 Support: {esc(SUPPORT_USERNAME)}\n🚀 Thank you for purchasing from <b>{esc(STORE_NAME)}</b>!")
        try:
            await edit(query, text, main_menu(tg_id))
        except Exception:
            await context.bot.send_message(tg_id, text, reply_markup=main_menu(tg_id), parse_mode="HTML")

        await notify_admins(context.bot,
                            f"💰 <b>NEW SALE!</b>\n\n🎮 {esc(product['name'])} - {esc(plan['plan_name'])}\n"
                            f"💰 {fmt_money(price)}\n👤 {uname(user)}\n🆔 <code>{tg_id}</code>")


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
    icons = {"approved": "✅", "pending": "⏳", "rejected": "❌", "expired": "⌛", "duplicate": "⚠️"}
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
                    btn("✍️ Custom", "amount:custom", "primary")],
                   [btn("⬅️ Back", "back:wallet")]]))


async def send_payment_details(context, chat_id: int, amount: float, user: Dict, query=None):
    """Creates an FMPay order and shows the QR. Balance is credited automatically."""
    async def fail(text: str):
        if query:
            await edit(query, text, back_btn("wallet"))
        else:
            await context.bot.send_message(chat_id, text, reply_markup=main_menu(user["telegram_id"]))

    if db.count_open_payments(user["id"], 10) >= 3:
        await fail("⚠️ You already have 3 unpaid payment sessions. Pay one of them or wait a few minutes.")
        return
    try:
        order = await asyncio.to_thread(
            fmpay_create_order, amount, f"{user.get('first_name') or 'user'} ({user['telegram_id']})")
    except Exception as e:
        logger.error(f"FMPay create order failed: {e}")
        await fail("❌ Payment gateway is not available right now. Please try again in a minute.")
        return

    payable = _to_float(order.get("payable_amount")) or amount
    tx_id = db.create_payment_tx(user["id"], amount, order["order_id"], payable, chat_id)
    caption = (f"💳 <b>Payment Details</b>\n\nAmount to pay: <b>{fmt_money(payable)}</b>\n"
               f"🧾 Order: <code>{esc(order['order_id'])}</code>\n"
               f"⏳ Valid for 5 minutes\n\n✅ <b>Steps:</b>\n"
               f"1. Scan the QR or tap <b>Pay Online</b>\n"
               f"2. Pay the <b>exact amount</b> shown\n"
               f"3. Balance is added automatically ⚡\n\n"
               f"Paid but not credited? Tap <b>Verify Payment</b> or verify with your UTR.")
    rows = []
    if order.get("checkout_url"):
        rows.append([btn("🌐 Pay Online", url=order["checkout_url"], style="success")])
    rows.append([btn("🔄 Verify Payment", f"verify_pay:{tx_id}", "primary")])
    rows.append([btn("🔢 Verify via UTR", f"utr_verify:{tx_id}", "primary")])
    rows.append(cancel_row())
    markup = kb(rows)

    if query:
        try:
            await query.message.delete()
        except Exception:
            pass
    qr_bytes = await asyncio.to_thread(fmpay_download, order.get("qr_url"))
    try:
        if qr_bytes:
            sent = await context.bot.send_photo(
                chat_id, InputFile(io.BytesIO(qr_bytes), filename="payment_qr.png"),
                caption=caption, reply_markup=markup, parse_mode="HTML")
        else:
            sent = await context.bot.send_message(chat_id, caption, reply_markup=markup, parse_mode="HTML")
        db.set_tx_message(tx_id, sent.message_id)
    except Exception as e:
        logger.error(f"Could not send payment screen: {e}")


async def manual_verify_payment(query, context, user: Dict, tx_id: int):
    tx = db.get_transaction(tx_id)
    if not tx or tx["user_id"] != user["id"]:
        await query.answer("❌ Payment not found.", show_alert=True)
        return
    if tx["status"] == "approved":
        await query.answer("✅ Already credited to your wallet.", show_alert=True)
        return
    if tx["status"] != "pending":
        await query.answer("⌛ This payment session has ended. Start a new one.", show_alert=True)
        return
    try:
        st = await asyncio.to_thread(fmpay_get_status, tx["order_id"])
    except Exception as e:
        logger.warning(f"verify {tx['order_id']} failed: {e}")
        await query.answer("⚠️ Could not check right now. Try again in a few seconds.", show_alert=True)
        return
    if st["status"] == "success":
        await query.answer("✅ Payment found! Crediting your wallet...")
        await settle_payment(context.bot, tx, st)
    elif st["status"] == "expired":
        await query.answer("⌛ Payment session expired.", show_alert=True)
        await settle_expired(context.bot, tx)
    else:
        await query.answer("⏳ Payment not received yet. Pay the exact amount shown - it verifies "
                           "automatically within seconds.", show_alert=True)


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
                   kb([[btn("🛒 Browse Store", "store", "primary")], [btn("⬅️ Back", "back:main")]]))
        return
    text = "📦 <b>My Orders</b> (last 10)\n\n"
    for o in orders:
        text += (f"🆔 <code>#{o['order_id']}</code> | <b>{esc(o['product_name'])}</b>\n"
                 f"   📋 {esc(o['plan_name'])} | {fmt_money(o['price'])}\n"
                 f"   📅 {fmt_time(o['created_at'])}\n")
        if o.get("key_code"):
            text += f"   🔑 <code>{esc(o['key_code'])}</code>\n"
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

async def show_leaderboard(query):
    text = head("🏆", "Leaderboard")
    buyers = db.get_top_buyers(5)
    text += "\n💎 <b>Top Buyers</b>\n"
    if buyers:
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, b in enumerate(buyers):
            text += f"{medals[i]} {mask_name(b)} - {fmt_money(b['spent'])} ({b['cnt']} orders)\n"
    else:
        text += "No purchases yet.\n"
    refs = db.get_top_referrers(5)
    text += f"\n{DIV}\n🎁 <b>Top Referrers</b>\n"
    if refs:
        for i, r in enumerate(refs, 1):
            text += f"{i}. {mask_name(r)} - {r['refs']} invites\n"
    else:
        text += "No referrals yet.\n"
    await edit(query, text, kb([[btn("⬅️ Back", "back:main", "primary")]]))


async def show_admin(query):
    pending = len(db.get_pending_transactions())
    maint = db.get_setting("maintenance") == "1"
    maint_label = "🔧 Maintenance: ON" if maint else "🔧 Maintenance: OFF"
    await edit(query,
               head("⚙️", "Admin Panel") +
               f"Pending payments: <b>{pending}</b>\nMaintenance: <b>{'ON 🔴' if maint else 'OFF 🟢'}</b>",
               kb([[btn("📊 Dashboard", "admin_dashboard", "primary"), btn("📈 Sales Report", "admin_report", "primary")],
                   [btn("👥 Users", "admin_users", "primary"), btn("📦 Orders", "admin_orders", "primary")],
                   [btn("🛍️ Products", "admin_products", "primary"), btn("🎁 Coupons", "admin_coupons", "primary")],
                   [btn("🏆 Top Stats", "admin_top", "primary"), btn("🔎 Find / Ban User", "admin_search", "danger")],
                   [btn("➕ Add Balance", "admin_add_balance", "success"), btn("➖ Deduct Balance", "admin_deduct", "danger")],
                   [btn("📢 Broadcast", "admin_broadcast", "primary"), btn("📝 Welcome Msg", "admin_welcome", "primary")],
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
               f"💰 Total Revenue: <b>{fmt_money(db.get_total_revenue())}</b>\n"
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
               f"🗓️ Last 30 days: <b>{fmt_money(m_sum)}</b> ({m_cnt} orders)\n"
               f"💰 All time: <b>{fmt_money(db.get_total_revenue())}</b> ({db.count_orders()} orders)",
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
        text += f"{'🟢' if on else '🔴'} <b>{esc(p['name'])}</b> ({esc(p['group_name'])})\n"
        rows.append([btn(f"✏️ {p['name'][:16]}", f"edit_prod:{p['id']}", "primary"),
                     btn("🙈 Hide" if on else "👁 Show", f"toggle_prod:{p['id']}"),
                     btn("🗑️", f"del_prod:{p['id']}", "danger")])
    rows.append([btn("➕ Add Product", "admin_add_product", "success")])
    rows.append([btn("⬅️ Back", "back:admin")])
    await edit(query, text, kb(rows))


async def show_edit_product(query, product_id: int):
    p = db.get_product(product_id)
    if not p:
        await edit(query, "❌ Product not found.", back_btn("admin_products"))
        return
    rows = []
    for pl in db.get_product_plans(product_id):
        rows.append([btn(f"✏️ {pl['plan_name']} - {fmt_money(pl['price'])}", f"edit_plan:{pl['id']}", "primary"),
                     btn("🗑️", f"del_plan:{pl['id']}", "danger")])
    rows.append([btn("➕ Add Plan", f"add_plan:{product_id}", "success")])
    rows.append([btn("⬅️ Back", "back:admin_products", "primary")])
    await edit(query,
               head("✏️", esc(p["name"])) + "Tap a plan to change its price, 🗑️ to remove it:", kb(rows))


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
# MESSAGE HANDLER (text input states)
# ═══════════════════════════════════════════════════════════════════

ADMIN_STATES = {"await_prod_name", "await_prod_group", "await_prod_plans", "await_prod_desc",
                "await_coupon_code", "await_broadcast", "await_admin_user_id", "await_admin_amount",
                "await_search", "await_plan_price", "await_welcome", "await_new_plan"}


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    uid = tg.id
    if not is_admin(uid) and flooded(uid, 0.6):
        return
    user = ensure_user(tg)
    blocked = gate_text(user, uid)
    if blocked:
        await update.message.reply_text(blocked)
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
        utr = text.replace(" ", "")
        if not (utr.isdigit() and len(utr) == 12):
            await msg.reply_text("❌ UTR must be exactly <b>12 digits</b>. Please send it again:", parse_mode="HTML")
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
        context.user_data["state"] = "await_prod_group"
        groups = ["🎮 Gaming", "💻 Software", "🎁 Gift Cards", "📺 Subscriptions", "🔐 License Keys", "🧩 Other"]
        rows = [[btn(g, f"prod_group:{g}")] for g in groups] + [cancel_row()]
        await msg.reply_text("<b>Step 2/4:</b> Select product group", reply_markup=kb(rows), parse_mode="HTML")

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
            except ValueError:
                errors.append(line)
                continue
            hours = guess_hours(name)
            if not hours:
                errors.append(f"{line}  (no hours/days in name)")
                continue
            plans.append((name, price_v, hours))
        if errors or not plans:
            await msg.reply_text("❌ Fix these lines and send again:\n" +
                                 "\n".join(esc(e) for e in (errors or ["No valid plans found."])) +
                                 "\n\nFormat: Name = Price  (e.g. 2 Days = 500)")
            return
        context.user_data["prod_plans"] = plans
        context.user_data["state"] = "await_prod_desc"
        await msg.reply_text("<b>Step 4/4:</b> Send product description (or <code>-</code> to skip):",
                             reply_markup=kb([cancel_row()]), parse_mode="HTML")

    elif state == "await_prod_desc":
        desc = "" if text == "-" else text
        name = context.user_data["prod_name"]
        pid = db.create_product(name, context.user_data["prod_group"], desc)
        for plan_name, price, hours in context.user_data["prod_plans"]:
            db.add_product_plan(pid, plan_name, price, hours)
        db.log_admin(uid, f"created product {pid} {name}")
        n = len(context.user_data["prod_plans"])
        context.user_data.clear()
        await msg.reply_text(f"✅ Product <b>{esc(name)}</b> created with {n} plans!",
                             reply_markup=main_menu(uid), parse_mode="HTML")

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
        await msg.reply_text(f"✅ Price updated to <b>{fmt_money(price)}</b>.",
                             reply_markup=main_menu(uid), parse_mode="HTML")

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

    # ─── admin: broadcast ───
    elif state == "await_broadcast":
        context.user_data.pop("state", None)
        body = msg.text_html or text
        sent = failed = 0
        await msg.reply_text("📢 Broadcasting... Please wait.")
        for u in db.get_all_users(limit=100000):
            if u["banned"]:
                continue
            try:
                await context.bot.send_message(
                    u["telegram_id"], RAW + f"📢 <b>Announcement from {esc(STORE_NAME)}</b>\n{DIV}\n{body}",
                    parse_mode="HTML")
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        db.log_admin(uid, f"broadcast sent={sent} failed={failed}")
        await msg.reply_text(f"✅ Broadcast complete!\nSent: {sent}\nFailed: {failed}", reply_markup=main_menu(uid))

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
        if not hours:
            await msg.reply_text("❌ Plan name must contain hours or days (e.g. <code>2 Days</code>).")
            return
        db.add_product_plan(pid, name, price, hours)
        db.log_admin(uid, f"added plan {name} = {price} to product {pid}")
        context.user_data.clear()
        await msg.reply_text(f"✅ Plan <b>{esc(name)}</b> added at {fmt_money(price)}.", reply_markup=main_menu(uid))

    else:
        await msg.reply_text(welcome_text(user), reply_markup=main_menu(uid), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════
# AUTO PAYMENT ENGINE  (FMPay verification, UTR registry)
# ═══════════════════════════════════════════════════════════════════

async def drop_qr_message(bot, tx: Dict):
    if tx.get("chat_id") and tx.get("message_id"):
        try:
            await bot.delete_message(tx["chat_id"], tx["message_id"])
        except Exception:
            pass


async def finalize_payment(bot, tx: Dict, utr: Optional[str]) -> str:
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
                tg, f"✅ <b>PAYMENT RECEIVED</b>\n\n💰 {fmt_money(tx['amount'])} added to your wallet!\n"
                    f"🔢 UTR: <code>{esc(utr or 'N/A')}</code>\n\nThank you for choosing <b>{esc(STORE_NAME)}</b>.",
                reply_markup=main_menu(tg), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to notify user {tg}: {e}")
        db.log_admin(0, f"auto-credited tx {tx['id']} ({tx['amount']}) to {tg} utr={utr}")
        await notify_admins(
            bot, f"💳 <b>AUTO PAYMENT</b> #{tx['id']}\n\n👤 {uname(u)}\n🆔 <code>{tg}</code>\n"
                 f"💰 <b>{fmt_money(tx['amount'])}</b>\n🔢 UTR: <code>{esc(utr or 'N/A')}</code>\n"
                 f"🧾 Order: <code>{esc(tx.get('order_id') or '')}</code>")
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


async def settle_payment(bot, tx: Dict, st: Dict) -> str:
    paid = st.get("amount")
    if paid is not None and paid + 0.011 < float(tx["amount"]):
        if db.set_tx_status(tx["id"], "rejected", ("pending", "expired")):
            await drop_qr_message(bot, tx)
            await notify_admins(bot, f"⚠️ <b>SHORT PAYMENT</b> tx #{tx['id']}: expected "
                                     f"{fmt_money(tx['amount'])}, received {fmt_money(paid)}.\n"
                                     f"Order: <code>{esc(tx.get('order_id') or '')}</code>")
            u = db.get_user_by_id(tx["user_id"])
            if u:
                try:
                    await bot.send_message(
                        u["telegram_id"], f"⚠️ Payment amount did not match. Contact {esc(SUPPORT_USERNAME)}.",
                        parse_mode="HTML")
                except Exception:
                    pass
        return "short"
    return await finalize_payment(bot, tx, st.get("utr"))


async def settle_expired(bot, tx: Dict):
    if not db.set_tx_status(tx["id"], "expired"):
        return
    await drop_qr_message(bot, tx)
    u = db.get_user_by_id(tx["user_id"])
    if not u:
        return
    try:
        await bot.send_message(
            u["telegram_id"],
            f"⌛ <b>Payment session expired</b>\n\nOrder <code>{esc(tx.get('order_id') or '')}</code> was not paid in time.\n"
            f"If you already paid, verify with your UTR below.",
            reply_markup=kb([[btn("🔢 Verify via UTR", f"utr_verify:{tx['id']}", "primary")],
                             [btn("💳 Add Balance", "add_balance", "success")]]),
            parse_mode="HTML")
    except Exception:
        pass


async def payment_poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Checks every unpaid payment with FMPay and credits it the moment it is paid."""
    for tx in db.get_pending_transactions()[:15]:
        try:
            st = await asyncio.to_thread(fmpay_get_status, tx["order_id"])
        except Exception as e:
            logger.warning(f"poll {tx['order_id']} failed: {e}")
            continue
        if st["status"] == "success":
            await settle_payment(context.bot, tx, st)
        elif st["status"] == "expired":
            await settle_expired(context.bot, tx)
        await asyncio.sleep(0.3)


async def verify_by_utr(context, msg, user: Dict, tx_id: Optional[int], utr: str):
    """User sends a UTR -> look it up in FMPay's received payments and credit it (once)."""
    back = main_menu(user["telegram_id"])
    tx = db.get_transaction(tx_id) if tx_id else None
    if not tx or tx["user_id"] != user["id"] or not tx.get("order_id"):
        await msg.reply_text("❌ Payment session not found. Please start Add Balance again.", reply_markup=back)
        return
    if db.utr_registered(utr):
        await msg.reply_text("❌ This UTR has already been used.", reply_markup=back)
        return
    wait = await msg.reply_text("🔎 Checking your payment...")
    try:
        rec = await asyncio.to_thread(fmpay_find_by_utr, utr)
    except Exception as e:
        logger.warning(f"UTR lookup failed: {e}")
        await msg.reply_text("⚠️ Could not check right now. Please try again in a minute.", reply_markup=back)
        return
    finally:
        try:
            await wait.delete()
        except Exception:
            pass
    if not rec:
        await msg.reply_text(
            "❌ This UTR was not found in received payments yet.\nIf you just paid, wait a minute and try again, "
            f"or contact {esc(SUPPORT_USERNAME)}.", reply_markup=back, parse_mode="HTML")
        return
    rec_order = str(rec.get("order_id") or "")
    target = tx if rec_order == tx["order_id"] else (db.get_tx_by_order(rec_order) if rec_order else None)
    if not target or target["user_id"] != user["id"]:
        db.log_admin(0, f"UTR {utr} claimed by {user['telegram_id']} does not match their orders")
        await msg.reply_text("❌ This payment does not belong to any of your payment sessions.", reply_markup=back)
        return
    paid = _to_float(rec.get("amount"))
    if paid is not None and paid + 0.011 < float(target["amount"]):
        await msg.reply_text(f"❌ Paid amount does not match. Contact {esc(SUPPORT_USERNAME)}.",
                             reply_markup=back, parse_mode="HTML")
        return
    res = await finalize_payment(context.bot, target, utr)
    if res == "done":
        await msg.reply_text("✅ This payment was already credited to your wallet.", reply_markup=back)
    elif res == "invalid":
        await msg.reply_text(f"❌ This payment can't be credited. Contact {esc(SUPPORT_USERNAME)}.",
                             reply_markup=back, parse_mode="HTML")


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
    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN missing. Paste it in BOT_TOKEN_VALUE at the top of storebot.py (or .env).")
    if not ADMIN_IDS:
        sys.exit("ADMIN ID missing. Set ADMIN_ID_VALUE at the top of storebot.py (or ADMIN_IDS in .env).")
    if not FMPAY_API_KEY:
        sys.exit("FMPay API key missing. Set FMPAY_API_KEY_VALUE at the top of storebot.py.")
    if not API_HEADERS["X-API-Key"] or not API_HEADERS["M-Key"]:
        logger.warning("API_KEY / API_MKEY missing in .env - key delivery will fail!")

    bot = BoldBot(token=BOT_TOKEN, defaults=Defaults(parse_mode="HTML"))
    app = Application.builder().bot(bot).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(expire_job, interval=300, first=60)
        app.job_queue.run_repeating(payment_poll_job, interval=PAYMENT_POLL_SEC, first=5)
    else:
        sys.exit("job-queue missing (needed for auto payment checks): "
                 "pip install 'python-telegram-bot[job-queue]'")

    logger.info(f"🚀 {STORE_NAME} Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
