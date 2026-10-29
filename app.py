from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import json
import secrets
import os
import io
import hashlib
import hmac
import html as html_lib
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent


def load_local_environment():
    env_path = BASE_DIR / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key:
            os.environ.setdefault(key, value)


load_local_environment()

DB_PATH = BASE_DIR / "mood_cafe.db"
PAYMENT_SLIP_DIR = BASE_DIR / "uploads" / "payment_slips"
MENU_UPLOAD_DIR = BASE_DIR / "static" / "uploads" / "menu"
MAX_MENU_IMAGE_BYTES = 5 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
RESET_EMAIL = "nattatidahotarat@gmail.com"
EMAIL_CONFIG_PATH = BASE_DIR / ".email_config.json"
RESET_CODE_TTL_SECONDS = 10 * 60
RESET_CODE_COOLDOWN_SECONDS = 60
RESET_CODE_MAX_ATTEMPTS = 5
LOYALTY_STAMPS_PER_REWARD = 10
LOYALTY_REWARD_PRICE_LIMIT = 100
BESTSELLER_MENU_NAMES = [
    "Gold Matcha Latte",
    "Caramel Macchiato",
    "Pistachio Chocolate Frappe",
    "Strawberry Yogurt Smoothie",
    "Americano",
    "ชาหอมหมื่นลี้",
]
app.jinja_env.auto_reload = True


@app.after_request
def prevent_stale_page_cache(response):
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

# Keep sessions valid across restarts. Set MOOD_CAFE_SECRET_KEY in production;
# otherwise persist a locally generated key beside the database.
app.secret_key = os.environ.get("MOOD_CAFE_SECRET_KEY")
if not app.secret_key:
    secret_path = BASE_DIR / ".secret_key"
    try:
        app.secret_key = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        app.secret_key = secrets.token_hex(32)
        secret_path.write_text(app.secret_key, encoding="utf-8")


# รูปแบบเครื่องดื่มและหมวดหมู่
STYLE_NAMES = {
    "hot": "ร้อน",
    "iced": "เย็น",
    "blended": "ปั่น"
}

CATEGORY_NAMES = {
    "tea": "ชา",
    "coffee": "กาแฟ",
    "milk": "นม",
    "fruit": "ผลไม้และโซดา"
}

MOOD_NAMES = {
    "happy": "มีความสุขแฮปปี้",
    "sad": "เศร้ามากวันนี้",
    "sleepy": "ง่วงจะร่วงแล้ว",
    "stress": "เครียดหัวจะระเบิด",
    "energy": "อยากมีพลัง อยากมีเอเนอร์จี้บูสๆ"
}

MOOD_REASONS = {
    "happy": "วันอารมณ์ดีๆแบบนี้ ลองเลือกเครื่องดื่มที่ชอบมาเพิ่มสีสันให้วันของคุณกันนะ",
    "sad": "วันนี้อย่าลืมใจดีกับตัวเอง ลองเลือกเครื่องดื่มแก้วโปรดเป็นความสุขเล็ก ๆ ให้ตัวเองนะ",
    "sleepy": "ถ้าอยากเติมความสดชื่น ลองเลือกเครื่องดื่มที่เข้ากับช่วงเวลาของคุณได้เลย",
    "stress": "พักจากเรื่องวุ่น ๆ สักครู่ แล้วเลือกเครื่องดื่มที่เหมาะกับช่วงเวลาพักของคุณนะ",
    "energy": "พร้อมลุยกับวันใหม่แล้ว! ลองเลือกเครื่องดื่มที่ให้ความรู้สึกสดใสในแบบของคุณ"
}

SWEETNESS_OPTIONS = ["0", "25", "50", "75", "100"]
MAX_MENU_QUANTITY = 20
VAT_RATE = Decimal("0.07")
MONEY_QUANTUM = Decimal("0.01")


def price_including_vat(price):
    gross = Decimal(str(price)) * (Decimal("1.00") + VAT_RATE)
    # Counter prices and cash change should be in whole baht.
    return float(gross.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def included_vat_from_gross(gross):
    amount = Decimal(str(gross)) * VAT_RATE / (Decimal("1.00") + VAT_RATE)
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def format_number_with_commas(value, decimals=None):
    try:
        amount = Decimal(str(value if value is not None else 0))
        if decimals is not None:
            digits = max(0, int(decimals))
            quantum = Decimal("1").scaleb(-digits)
            amount = amount.quantize(quantum, rounding=ROUND_HALF_UP)
            return format(amount, f",.{digits}f")
        rendered = format(amount, ",f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return str(value if value is not None else "")


app.jinja_env.filters["number"] = format_number_with_commas


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                mood TEXT,
                note TEXT,
                items_json TEXT,
                total REAL,
                status TEXT DEFAULT 'รับออร์เดอร์แล้วค่ะ',
                tracking_code TEXT,
                user_id INTEGER,
                payment_status TEXT DEFAULT 'paid',
                payment_slip_path TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                phone TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT,
                is_admin INTEGER DEFAULT 0,
                email_verified INTEGER NOT NULL DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                earned_order_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                redeemed_order_id INTEGER,
                notified_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_codes (
                email TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_verification_codes (
                email TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS menu_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                emoji TEXT NOT NULL,
                prices_json TEXT NOT NULL,
                moods_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT,
                image_filename TEXT
            )
        """)

        # ตรวจสอบและอัปเกรดคอลัมน์เดิมสำหรับฐานข้อมูลเก่า
        user_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        ]
        if "is_admin" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        if "phone" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        if "email_verified" not in user_columns:
            # Keep existing members active; newly registered accounts explicitly start unverified.
            conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")
        if "loyalty_stamps" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN loyalty_stamps INTEGER NOT NULL DEFAULT 0")

        order_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()
        ]
        loyalty_migration = "loyalty_processed" not in order_columns
        if "user_id" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN user_id INTEGER")
        if "payment_status" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN payment_status TEXT DEFAULT 'paid'")
            conn.execute("UPDATE orders SET payment_status = 'paid' WHERE payment_status IS NULL")
        if "payment_slip_path" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN payment_slip_path TEXT")
        for column, declaration in {
            "customer_name": "TEXT",
            "customer_phone": "TEXT",
            "payment_method": "TEXT NOT NULL DEFAULT 'promptpay'",
            "cash_received": "REAL",
            "change_given": "REAL",
            "discount_amount": "REAL NOT NULL DEFAULT 0",
            "loyalty_reward_id": "INTEGER",
            # Existing orders predate loyalty; only later paid orders are counted.
            "loyalty_processed": "INTEGER NOT NULL DEFAULT 1",
        }.items():
            if column not in order_columns:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {column} {declaration}")
        if loyalty_migration:
            totals = {}
            rows = conn.execute(
                "SELECT id, user_id, items_json, created_at, payment_status, payment_method FROM orders ORDER BY id"
            ).fetchall()
            for order_id, user_id, raw_items, created_at, payment_status, payment_method in rows:
                if not user_id or payment_status != "paid" or payment_method == "loyalty_reward":
                    continue
                try:
                    items = json.loads(raw_items or "[]")
                except (TypeError, ValueError):
                    items = []
                quantity = 0
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            try:
                                quantity += max(0, int(float(item.get("quantity", 0))))
                            except (ValueError, TypeError, OverflowError):
                                continue
                if quantity:
                    earned, stamps, latest_order = totals.get(user_id, (0, 0, order_id))
                    full_stamps = stamps + quantity
                    new_rewards, remaining = divmod(full_stamps, LOYALTY_STAMPS_PER_REWARD)
                    totals[user_id] = (earned + new_rewards, remaining, order_id)
            for user_id, (reward_count, stamps, earned_order_id) in totals.items():
                conn.execute("UPDATE users SET loyalty_stamps=? WHERE id=?", (stamps, user_id))
                for _ in range(reward_count):
                    conn.execute(
                        "INSERT INTO loyalty_rewards (user_id, earned_order_id, created_at) VALUES (?, ?, ?)",
                        (user_id, earned_order_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    )

        menu_columns = [row[1] for row in conn.execute("PRAGMA table_info(menu_items)").fetchall()]
        if "image_filename" not in menu_columns:
            conn.execute("ALTER TABLE menu_items ADD COLUMN image_filename TEXT")


init_db()



# สร้างข้อมูลเครื่องดื่มหนึ่งเมนู
def drink(name, category, prices, moods):
    return {
        "name": name,
        "category": category,
        "prices": prices,
        "moods": moods,
        "sweetness_options": SWEETNESS_OPTIONS
    }


# ฐานข้อมูลเมนูเครื่องดื่ม
DRINKS = [
    # หมวดชา
    drink("Thai Tea", "tea", {"iced": 60, "blended": 70}, ["happy", "sad"]),
    drink("Matcha Latte", "tea", {"hot": 75, "iced": 85, "blended": 95}, ["stress", "happy"]),
    drink("Peach Tea", "tea", {"iced": 65}, ["happy", "sad", "energy"]),
    drink("Earl Grey Tea", "tea", {"hot": 60, "iced": 70}, ["stress", "happy"]),
    drink("Honey Lemon Tea", "tea", {"hot": 55, "iced": 65}, ["sleepy", "energy"]),
    drink("ชาหอมหมื่นลี้", "tea", {"hot": 79, "iced": 89}, ["happy", "stress"]),
    drink("มัทฉะสตรอว์เบอร์รี", "tea", {"iced": 70, "blended": 80}, ["happy", "sad"]),

    # หมวดกาแฟ
    drink("Latte", "coffee", {"hot": 65, "iced": 75, "blended": 85}, ["sleepy", "energy"]),
    drink("Americano", "coffee", {"hot": 50, "iced": 60}, ["sleepy", "energy"]),
    drink("Cappuccino", "coffee", {"hot": 70, "iced": 80}, ["happy", "stress"]),
    drink("Caramel Macchiato", "coffee", {"hot": 85, "iced": 95}, ["happy", "sad"]),
    drink("Mocha", "coffee", {"hot": 80, "iced": 90, "blended": 100}, ["happy", "sad", "stress"]),
    drink("Espresso", "coffee", {"hot": 50}, ["sleepy", "energy"]),
    drink("Caramel Latte", "coffee", {"hot": 85, "iced": 95, "blended": 110}, ["happy", "sad"]),
    drink("Hazelnut Latte", "coffee", {"hot": 90, "iced": 100, "blended": 115}, ["stress", "happy"]),
    drink("White Chocolate Mocha", "coffee", {"hot": 100, "iced": 110, "blended": 125}, ["happy", "sad"]),
    drink("Affogato", "coffee", {"iced": 120}, ["happy", "energy"]),
    drink("Cold Brew", "coffee", {"iced": 95}, ["sleepy", "energy"]),
    drink("Signature Coffee", "coffee", {"hot": 129, "iced": 149, "blended": 169}, ["happy", "energy"]),

    # หมวดนม
    drink("Strawberry Milk", "milk", {"iced": 55, "blended": 65}, ["happy", "sad"]),
    drink("Cocoa", "milk", {"hot": 55, "iced": 65, "blended": 75}, ["sad", "stress"]),
    drink("Vanilla Milk", "milk", {"hot": 50, "iced": 60}, ["stress", "sad"]),
    drink("Taro Milk", "milk", {"iced": 60, "blended": 70}, ["happy", "sad"]),
    drink("Chocolate Milkshake", "milk", {"blended": 100}, ["happy", "sad"]),
    drink("คาราเมลนมสด", "milk", {"hot": 50, "iced": 65, "blended": 75}, ["happy", "sad", "stress"]),
    drink("Hojicha Latte", "milk", {"hot": 85, "iced": 95, "blended": 110}, ["stress", "happy"]),
    drink("Ube Matcha", "milk", {"iced": 105, "blended": 125}, ["happy", "sad"]),

    # เมนูชาเพิ่มเติม
    drink("Rose Tea", "tea", {"hot": 75, "iced": 85}, ["stress", "happy"]),
    drink("Premium Dark Matcha", "tea", {"iced": 129}, ["stress", "energy"]),
    drink("Thai Tea Cream Cheese", "tea", {"iced": 99, "blended": 119}, ["happy", "sad"]),
    drink("Strawberry Matcha Cream", "tea", {"iced": 139, "blended": 159}, ["happy", "sad"]),

    # หมวดนมและเมนูพรีเมียม
    drink("Honey Milk", "milk", {"hot": 55, "iced": 65, "blended": 75}, ["stress", "sad"]),
    drink("Brown Sugar Milk", "milk", {"iced": 79, "blended": 95}, ["happy", "sad"]),
    drink("Pistachio Milk", "milk", {"hot": 99, "iced": 119, "blended": 139}, ["happy", "stress"]),
    drink("Dark Chocolate", "milk", {"hot": 85, "iced": 95, "blended": 115}, ["sad", "stress"]),
    drink("Chocolate Mint", "milk", {"hot": 89, "iced": 99, "blended": 119}, ["happy", "energy"]),
    drink("Premium Chocolate Frappe", "milk", {"blended": 159}, ["happy", "sad"]),
    drink("Gold Matcha Latte", "tea", {"iced": 169}, ["happy", "energy"]),
    drink("Caramel Crown Frappe", "coffee", {"blended": 179}, ["happy", "sad"]),
    drink("Strawberry Cheesecake Frappe", "milk", {"blended": 189}, ["happy", "sad"]),
    drink("Pistachio Chocolate Frappe", "milk", {"blended": 195}, ["happy", "stress"]),
    drink("Mood Luxury Frappe", "milk", {"blended": 200}, ["happy", "sad"]),

    # หมวดผลไม้และโซดา
    drink("Strawberry Soda", "fruit", {"iced": 55}, ["happy", "sad", "energy"]),
    drink("Blue Lemon Soda", "fruit", {"iced": 60}, ["sleepy", "energy", "happy"]),
    drink("Passion Fruit Soda", "fruit", {"iced": 65}, ["sleepy", "energy"]),
    drink("Kiwi Lime Soda", "fruit", {"iced": 70}, ["stress", "energy"]),
    drink("Mixed Berry Smoothie", "fruit", {"blended": 110}, ["happy", "sad"]),
    drink("Lychee Soda", "fruit", {"iced": 65}, ["happy", "energy"]),
    drink("Mango Smoothie", "fruit", {"blended": 95}, ["happy", "energy"]),
    drink("Strawberry Yogurt Smoothie", "fruit", {"blended": 110}, ["happy", "sad"]),
    drink("Peach Yuzu Soda", "fruit", {"iced": 95}, ["sleepy", "energy"]),
    drink("Tropical Fruit Smoothie", "fruit", {"blended": 125}, ["happy", "energy"]),
    drink("Premium Berry Smoothie", "fruit", {"blended": 149}, ["happy", "sad"])
]


# สร้างรหัสรูปภาพของแต่ละเมนู
MENU_IMAGE_KEYS = {
    item["name"]: {
        "Peach Yuzu Soda": "Peach Tea",
        "Tropical Fruit Smoothie": "Mango Smoothie",
        "Premium Berry Smoothie": "Mixed Berry Smoothie",
    }.get(item["name"], item["name"])
    for item in DRINKS
}


def normalize_order_items(raw_items):
    try:
        items = json.loads(raw_items or "[]") if isinstance(raw_items, (str, bytes)) else raw_items
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(items, list):
        return []
    normalized = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        try:
            item["price"] = max(0.0, round(float(item.get("price", 0) or 0), 2))
            item["quantity"] = max(1, min(99, int(float(item.get("quantity", 1) or 1))))
        except (TypeError, ValueError, OverflowError):
            item["price"] = 0.0
            item["quantity"] = 1
        item["name"] = str(item.get("name") or "เมนู")
        normalized.append(item)
    return normalized


def get_session_cart():
    raw_cart = session.get("cart", [])
    raw_recommend_cart = session.get("recommend_cart", [])
    if not isinstance(raw_cart, list):
        raw_cart = []
    if not isinstance(raw_recommend_cart, list):
        raw_recommend_cart = []

    # Repair older session entries that may be missing fields added by newer versions.
    cart = []
    for raw_item in raw_cart + raw_recommend_cart:
        if not isinstance(raw_item, dict) or not raw_item.get("name"):
            continue
        item = dict(raw_item)
        try:
            price = round(float(item.get("price")), 2)
            quantity = int(float(item.get("quantity", 1)))
        except (TypeError, ValueError, OverflowError):
            continue
        if price < 1:
            continue
        if not item.get("price_includes_vat"):
            price = price_including_vat(price)
        item["price"] = price
        item["price_includes_vat"] = True
        item["quantity"] = max(1, min(quantity, MAX_MENU_QUANTITY))
        item["name"] = str(item["name"])
        item["style_name"] = str(item.get("style_name") or "เย็น")
        item["sweet"] = str(item.get("sweet") or "ความหวาน 50%")
        item["ice_separate"] = bool(item.get("ice_separate", False))
        item["image_key"] = item.get("image_key") or MENU_IMAGE_KEYS.get(item["name"], "menu-default")
        item["category"] = item.get("category") or "MENU"

        existing = next((current for current in cart
                         if current.get("name") == item["name"]
                         and current.get("style_name") == item["style_name"]
                         and current.get("sweet") == item["sweet"]
                         and current.get("ice_separate") == item["ice_separate"]), None)
        if existing:
            existing["quantity"] = min(99, existing["quantity"] + item["quantity"])
        else:
            cart.append(item)

    # The cap applies to the drink name across all sizes and customizations.
    remaining_by_name = {}
    capped_cart = []
    for item in cart:
        remaining = remaining_by_name.setdefault(item["name"], MAX_MENU_QUANTITY)
        item["quantity"] = min(item["quantity"], remaining)
        remaining_by_name[item["name"]] = remaining - item["quantity"]
        if item["quantity"] > 0:
            capped_cart.append(item)
    cart = capped_cart

    session["cart"] = cart
    session.pop("recommend_cart", None)
    if not session.get("cart_mood") and session.get("recommend_cart_mood"):
        session["cart_mood"] = session["recommend_cart_mood"]
        session["cart_note"] = session.get("recommend_cart_note", "")
    session.pop("recommend_cart_mood", None)
    session.pop("recommend_cart_note", None)
    return cart


def seed_menu_items():
    with sqlite3.connect(DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO menu_items (name,category,emoji,prices_json,moods_json,active,updated_at) VALUES (?,?,?,?,?,1,?)",
                [(d["name"], d["category"], d.get("emoji", ""), json.dumps(d["prices"], ensure_ascii=False), json.dumps(d["moods"], ensure_ascii=False), datetime.now().isoformat(timespec="seconds")) for d in DRINKS]
            )


def get_drinks(active_only=True, include_vat=True):
    query = "SELECT * FROM menu_items" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()
    drinks = []
    for row in rows:
        prices = json.loads(row["prices_json"])
        if include_vat:
            prices = {code: price_including_vat(price) for code, price in prices.items()}
        drinks.append({"id": row["id"], "name": row["name"], "category": row["category"], "emoji": "", "image_key": MENU_IMAGE_KEYS.get(row["name"], "menu-default"), "image_filename": row["image_filename"], "prices": prices, "moods": json.loads(row["moods_json"]), "sweetness_options": SWEETNESS_OPTIONS, "active": row["active"]})
    return drinks


def get_loyalty_summary(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT loyalty_stamps FROM users WHERE id=?", (user_id,)).fetchone()
        reward_count = conn.execute(
            "SELECT COUNT(*) FROM loyalty_rewards WHERE user_id=? AND redeemed_order_id IS NULL",
            (user_id,),
        ).fetchone()[0]
        next_reward = conn.execute(
            "SELECT id FROM loyalty_rewards WHERE user_id=? AND redeemed_order_id IS NULL ORDER BY id LIMIT 1",
            (user_id,),
        ).fetchone()
    return {
        "stamps": int(user["loyalty_stamps"] or 0) if user else 0,
        "needed": LOYALTY_STAMPS_PER_REWARD,
        "rewards": int(reward_count),
        "next_reward_id": next_reward[0] if next_reward else "",
        "price_limit": LOYALTY_REWARD_PRICE_LIMIT,
    }


@app.context_processor
def inject_menu_image_keys():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT name, image_filename FROM menu_items WHERE image_filename IS NOT NULL").fetchall()
    image_urls = {row["name"]: url_for("static", filename=f"uploads/menu/{row['image_filename']}") for row in rows}
    return {"menu_image_keys": MENU_IMAGE_KEYS, "menu_image_urls": image_urls, "current_year": datetime.now().year}


seed_menu_items()


@app.route("/")
def home():
    return render_template(
        "index.html",
        username=session.get("username"),
        is_admin=session.get("is_admin", 0)
    )


@app.route("/choose")
def choose():
    if not session.get("user_id"):
        return redirect(url_for("home"))
    return render_template("choice.html")


@app.route("/recommend-form")
def recommend_form():
    if not session.get("user_id"):
        return redirect(url_for("home"))
    return render_template(
        "recommend.html",
        category_names=CATEGORY_NAMES,
        style_names=STYLE_NAMES
    )


@app.route("/menu")
def menu():
    if not session.get("user_id"):
        return redirect(url_for("home"))

    drinks = get_drinks()
    notify_pending_loyalty_emails(session["user_id"])
    loyalty = get_loyalty_summary(session["user_id"])
    return render_template(
        "menu.html",
        drinks=drinks,
        category_names=CATEGORY_NAMES,
        style_names=STYLE_NAMES,
        bestseller_names=BESTSELLER_MENU_NAMES,
        loyalty=loyalty,
    )


@app.route("/cart")
def view_cart():
    if not session.get("user_id"):
        return redirect(url_for("home"))

    cart = get_session_cart()
    total = round(sum(item["price"] * item["quantity"] for item in cart), 2)

    return render_template("cart.html", cart=cart, total=total, cart_note=session.get("cart_note", ""))


@app.route("/recommend-cart")
def view_recommend_cart():
    return redirect(url_for("view_cart"))


@app.route("/recommend", methods=["POST"])
def recommend():
    if not session.get("user_id"):
        return redirect(url_for("home"))

    mood = request.form.get("mood", "happy")
    budget_text = request.form.get("budget", "200")
    category = request.form.get("category", "all")
    style = request.form.get("style", "any")
    sweetness = request.form.get("sweetness", "50")
    ice_separate = request.form.get("ice_separate") == "yes"
    note = request.form.get("note", "").strip()

    if mood not in MOOD_NAMES:
        mood = "happy"

    try:
        budget = int(budget_text)
    except (TypeError, ValueError):
        budget = 200

    budget = max(50, min(budget, 200))

    if category not in CATEGORY_NAMES and category != "all":
        category = "all"

    if style not in STYLE_NAMES and style != "any":
        style = "any"

    if sweetness not in SWEETNESS_OPTIONS:
        sweetness = "50"

    results = []

    for item in get_drinks():
        if mood not in item["moods"]:
            continue

        if category != "all" and item["category"] != category:
            continue

        available_prices = item["prices"]

        if style != "any":
            if style not in available_prices:
                continue
            chosen_style = style
        else:
            chosen_style = min(available_prices, key=available_prices.get)

        price = available_prices[chosen_style]

        if price > budget:
            continue

        if sweetness not in item["sweetness_options"]:
            continue

        results.append({
            "name": item["name"],
            "emoji": "",
            "image_key": item.get("image_key", MENU_IMAGE_KEYS.get(item["name"], "menu-default")),
            "image_url": url_for("static", filename=f"uploads/menu/{item['image_filename']}") if item.get("image_filename") else None,
            "category": CATEGORY_NAMES[item["category"]],
            "price": price,
            "style_name": STYLE_NAMES[chosen_style],
            "sweet": f"ความหวาน {sweetness}%",
            "ice_separate": ice_separate if chosen_style == "iced" else False
        })

    results.sort(key=lambda item: item["price"])

    recommend_cart = get_session_cart()
    recommend_cart_total = round(sum(item["price"] * item["quantity"] for item in recommend_cart), 2)

    return render_template(
        "result.html",
        mood_code=mood,
        mood=MOOD_NAMES[mood],
        reason=MOOD_REASONS[mood],
        drinks=results,
        budget=budget,
        category_name=CATEGORY_NAMES.get(category, "ทุกหมวด"),
        style_name=STYLE_NAMES.get(style, "ทุกรูปแบบ"),
        sweetness=sweetness,
        ice_separate=ice_separate if style == "iced" else False,
        note=note,
        recommend_cart=recommend_cart,
        recommend_cart_total=recommend_cart_total
    )


def prepare_payment(order_items, mood_code, note, cart_source):
    total = round(sum(item["price"] * item["quantity"] for item in order_items), 2)

    session["pending_order"] = {
        "items": order_items,
        "mood": MOOD_NAMES.get(mood_code, mood_code),
        "note": note,
        "total": total,
        "tracking_code": "MC-" + secrets.token_hex(4).upper(),
        "cart_source": cart_source
    }

    return render_template("payment.html", total=total, cart_source=cart_source, qr_url=url_for("static", filename="images/promptpay-qr.png", v="payment-qr-1"), error="")


@app.route("/confirm-order", methods=["POST"])
def confirm_order():
    if not session.get("user_id"):
        return redirect(url_for("home"))

    order_items = get_session_cart()
    if not order_items:
        return "ตะกร้ายังว่างอยู่ กรุณาเลือกเครื่องดื่มก่อนนะคะ", 400

    mood_code = session.get("cart_mood", "")
    note = request.form.get("note", session.get("cart_note", "")).strip()[:240]
    session["cart_note"] = note

    return prepare_payment(order_items, mood_code, note, "menu")


@app.route("/confirm-recommend-order", methods=["POST"])
def confirm_recommend_order():
    if not session.get("user_id"):
        return redirect(url_for("home"))

    order_items = get_session_cart()
    if not order_items:
        return "ตะกร้ายังว่างอยู่ กรุณาเลือกเครื่องดื่มก่อนนะคะ", 400

    mood_code = session.get("cart_mood", "")
    note = session.get("cart_note", "")

    return prepare_payment(order_items, mood_code, note, "recommend")


def read_payment_slip(upload):
    if upload is None or not upload.filename:
        raise ValueError("กรุณาแนบสลิปก่อนส่งคำสั่งซื้อ")
    data = upload.stream.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("ไฟล์สลิปต้องมีขนาดไม่เกิน 5 MB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = ".png"
    elif data.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        extension = ".webp"
    else:
        raise ValueError("รองรับไฟล์รูปภาพ PNG, JPG หรือ WEBP เท่านั้น")
    return data, extension


@app.route("/complete-payment", methods=["POST"])
@app.route("/complete-demo-payment", methods=["POST"])
def submit_payment():
    if not session.get("user_id"):
        return redirect(url_for("home"))
    cart_source = request.form.get("cart_source", "menu")
    pending_key = "pending_order"
    pending = (session.get(pending_key)
               or session.get(f"pending_order_{cart_source}")
               or session.get("pending_order_menu")
               or session.get("pending_order_recommend"))
    if not pending:
        return "ไม่พบคำสั่งซื้อ กรุณาเริ่มสั่งซื้อใหม่ค่ะ", 400
    try:
        slip_data, extension = read_payment_slip(request.files.get("payment_slip"))
    except ValueError as exc:
        return render_template(
            "payment.html", total=pending["total"], cart_source=cart_source,
            qr_url=url_for("static", filename="images/promptpay-qr.png", v="payment-qr-1"),
            error=str(exc)
        ), 400

    PAYMENT_SLIP_DIR.mkdir(parents=True, exist_ok=True)
    slip_filename = secrets.token_hex(16) + extension
    slip_path = PAYMENT_SLIP_DIR / slip_filename
    slip_path.write_bytes(slip_data)
    order_items = pending["items"]
    mood = pending["mood"]
    note = pending["note"]
    total = pending["total"]
    tracking_code = pending["tracking_code"]
    cart_source = pending.get("cart_source", "menu")
    user_id = session.get("user_id")
    status = "รอตรวจสอบการชำระเงิน"

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO orders
                (user_id, created_at, mood, note, items_json, total, status,
                 tracking_code, payment_status, payment_slip_path, loyalty_processed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0)
            """, (
                user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mood,
                note, json.dumps(order_items, ensure_ascii=False), total, status,
                tracking_code, slip_filename
            ))
    except Exception:
        slip_path.unlink(missing_ok=True)
        raise

    order = {"items": order_items, "mood": mood, "note": note, "total": total,
             "tracking_code": tracking_code, "status": status, "payment_status": "pending"}
    session.pop("cart", None)
    session.pop("cart_mood", None)
    session.pop("cart_note", None)
    session.pop("recommend_cart", None)
    session.pop("recommend_cart_mood", None)
    session.pop("recommend_cart_note", None)
    session.pop(pending_key, None)
    return render_template("order.html", order=order, total=total, note=note,
                           mood=mood, tracking_code=tracking_code)

@app.route("/add-to-cart", methods=["POST"])
def add_to_cart():
    drink_name = request.form.get("drink_name", "")
    style_name = request.form.get("style_name", "")
    sweetness = request.form.get("sweetness", "50")
    mood = request.form.get("mood", "")
    note = request.form.get("note", "").strip()
    ice_separate = request.form.get("ice_separate") == "yes"
    cart_source = request.form.get("cart_source", "menu")

    try:
        quantity = int(request.form.get("quantity", "1"))
        quantity = max(1, min(quantity, MAX_MENU_QUANTITY))
    except (ValueError, TypeError):
        quantity = 1

    style_codes = {name: code for code, name in STYLE_NAMES.items()}
    style_code = style_codes.get(style_name)

    drink_item = next((d for d in get_drinks() if d["name"] == drink_name), None)

    if (
        drink_item is None
        or style_code not in drink_item["prices"]
        or sweetness not in SWEETNESS_OPTIONS
    ):
        return jsonify({"error": "ข้อมูลเครื่องดื่มไม่ถูกต้อง"}), 400

    if not session.get("user_id"):
        return jsonify({"error": "กรุณาเข้าสู่ระบบก่อนสั่งเครื่องดื่ม"}), 401

    cart = get_session_cart()

    cart_item = {
        "name": drink_item["name"],
        "emoji": "",
        "image_key": drink_item.get("image_key", MENU_IMAGE_KEYS.get(drink_item["name"], "menu-default")),
        "image_url": url_for("static", filename=f"uploads/menu/{drink_item['image_filename']}") if drink_item.get("image_filename") else None,
        "category": CATEGORY_NAMES[drink_item["category"]],
        "style_name": style_name,
        "price": drink_item["prices"][style_code],
        "price_includes_vat": True,
        "sweet": f"ความหวาน {sweetness}%",
        "ice_separate": ice_separate and style_code == "iced",
        "quantity": quantity
    }

    menu_quantity = sum(item["quantity"] for item in cart if item["name"] == cart_item["name"])
    if menu_quantity + quantity > MAX_MENU_QUANTITY:
        return jsonify({"error": f"เมนู {cart_item['name']} สั่งได้สูงสุด {MAX_MENU_QUANTITY} แก้วต่อออร์เดอร์"}), 400

    existing = next(
        (
            item for item in cart
            if item["name"] == cart_item["name"]
            and item["style_name"] == cart_item["style_name"]
            and item["sweet"] == cart_item["sweet"]
            and item["ice_separate"] == cart_item["ice_separate"]
        ),
        None
    )

    if existing:
        existing["quantity"] += quantity
    else:
        cart.append(cart_item)

    session["cart"] = cart
    if mood or not session.get("cart_mood"):
        session["cart_mood"] = mood
        session["cart_note"] = note

    return jsonify({
        "cart": cart,
        "total": round(sum(item["price"] * item["quantity"] for item in cart), 2),
        "count": sum(item["quantity"] for item in cart)
    })


@app.route("/remove-from-cart", methods=["POST"])
def remove_from_cart():
    if not session.get("user_id"):
        return jsonify({"error": "กรุณาเข้าสู่ระบบก่อนแก้ไขตะกร้า"}), 401

    drink_name = request.form.get("drink_name", "")
    style_name = request.form.get("style_name", "")
    sweetness = request.form.get("sweetness", "")
    ice_separate = request.form.get("ice_separate") == "yes"
    cart = get_session_cart()

    for index, item in enumerate(cart):
        if (
            item["name"] == drink_name
            and item["style_name"] == style_name
            and item["sweet"] == sweetness
            and item["ice_separate"] == ice_separate
        ):
            cart.pop(index)
            break

    session["cart"] = cart

    return jsonify({
        "cart": cart,
        "total": round(sum(item["price"] * item["quantity"] for item in cart), 2),
        "count": sum(item["quantity"] for item in cart)
    })


@app.route("/clear-cart", methods=["POST"])
def clear_cart():
    if not session.get("user_id"):
        return redirect(url_for("home"))

    session.pop("cart", None)
    session.pop("cart_mood", None)
    session.pop("cart_note", None)
    session.pop("recommend_cart", None)
    session.pop("recommend_cart_mood", None)
    session.pop("recommend_cart_note", None)
    return redirect(url_for("view_cart"))


@app.route("/update-cart", methods=["POST"])
def update_cart():
    if not session.get("user_id"):
        return jsonify({"error": "กรุณาเข้าสู่ระบบก่อนแก้ไขตะกร้า"}), 401

    data = request.get_json(silent=True) or {}

    index = data.get("index")
    action = data.get("action")
    cart = get_session_cart()

    if not isinstance(index, int) or index < 0 or index >= len(cart):
        return jsonify({"error": "ไม่พบรายการสินค้า"}), 400

    if action == "increase":
        menu_quantity = sum(item["quantity"] for item in cart if item["name"] == cart[index]["name"])
        if menu_quantity >= MAX_MENU_QUANTITY:
            return jsonify({"error": f"เมนูนี้สั่งได้สูงสุด {MAX_MENU_QUANTITY} แก้วต่อออร์เดอร์"}), 400
        cart[index]["quantity"] += 1
    elif action == "decrease":
        cart[index]["quantity"] -= 1
        if cart[index]["quantity"] <= 0:
            cart.pop(index)
    elif action == "remove":
        cart.pop(index)
    else:
        return jsonify({"error": "คำสั่งไม่ถูกต้อง"}), 400

    session["cart"] = cart
    session.modified = True

    return jsonify({
        "cart": cart,
        "total": round(sum(item["price"] * item["quantity"] for item in cart), 2),
        "count": sum(item["quantity"] for item in cart)
    })


def load_receipt_order(tracking_code):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT orders.*, COALESCE(orders.customer_name, users.username, 'ลูกค้า') AS resolved_customer_name
               FROM orders LEFT JOIN users ON users.id = orders.user_id
               WHERE orders.tracking_code = ?""",
            (tracking_code.strip().upper(),)
        ).fetchone()
    if not row or (row["user_id"] != session.get("user_id") and not session.get("is_admin")):
        return None

    order = dict(row)
    order["items"] = normalize_order_items(order.get("items_json"))
    order["customer_name"] = order.get("resolved_customer_name") or order.get("customer_name") or "ลูกค้า"
    order["receipt_number"] = f"MC-R-{order['id']:06d}"
    return order


def receipt_template_values(order, pdf_mode=False):
    logo = BASE_DIR / "static" / "images" / "mood-cafe-logo-warm.png"
    logo_data_uri = "data:image/png;base64," + __import__("base64").b64encode(logo.read_bytes()).decode("ascii")
    subtotal = sum(
        (Decimal(str(item.get("price", 0))) * Decimal(str(item.get("quantity", 1)))
         for item in order["items"]),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    discount = Decimal(str(order.get("discount_amount") or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    shipping = Decimal("0.00")
    calculated_total = max(Decimal("0.00"), subtotal - discount + shipping)
    stored_total = order.get("total")
    receipt_total = Decimal(str(stored_total if stored_total is not None else calculated_total)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    vat = included_vat_from_gross(receipt_total)
    payment_method = {
        "cash": "เงินสดหน้าร้าน",
        "promptpay": "โอนผ่าน PromptPay / QR",
        "loyalty_reward": "ใช้สิทธิ์เครื่องดื่มฟรีจากแต้มสะสม",
    }.get(order.get("payment_method"), "โอนผ่าน PromptPay / QR" if order.get("payment_slip_path") else "PromptPay / โอนเงิน")
    payment_status_label = (
        "ชำระเงินแล้ว"
        if order.get("payment_status") == "paid" or order.get("payment_slip_path")
        else {
            "paid": "ชำระเงินแล้ว",
            "pending": "รอตรวจสอบการชำระเงิน",
            "rejected": "สลิปไม่ผ่านการตรวจสอบ",
        }.get(order.get("payment_status"), order.get("payment_status") or "ไม่ระบุ")
    )
    note_text = str(order.get("note") or "")
    item_count = max(1, len(order.get("items") or []))
    note_lines = (len(note_text) + 39) // 40 if note_text else 0
    receipt_page_height_mm = 118 + item_count * 12 + note_lines * 4
    return {
        "order": order,
        "receipt_page_height_mm": receipt_page_height_mm,
        "receipt_number": order["receipt_number"],
        "customer_name": order["customer_name"],
        "logo_data_uri": logo_data_uri,
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "vat": vat,
        "receipt_total": receipt_total,
        "payment_method": payment_method,
        "payment_status_label": payment_status_label,
        "cash_received": order.get("cash_received"),
        "change_given": order.get("change_given"),
        "back_url": url_for("admin_orders") if session.get("is_admin") else url_for("track_order", code=order["tracking_code"]),
        "pdf_mode": pdf_mode,
        "auto_print": request.args.get("print") == "1" and not pdf_mode,
    }


@app.route("/receipt/<tracking_code>")
def print_receipt(tracking_code):
    if not session.get("user_id"):
        return redirect(url_for("home"))
    order = load_receipt_order(tracking_code)
    if not order:
        return "ไม่พบออร์เดอร์", 404
    return render_template("receipt.html", **receipt_template_values(order))


@app.route("/receipt/<tracking_code>/pdf")
def download_receipt_pdf(tracking_code):
    if not session.get("user_id"):
        return redirect(url_for("home"))
    order = load_receipt_order(tracking_code)
    if not order:
        return "ไม่พบออร์เดอร์", 404
    html = render_template("receipt.html", **receipt_template_values(order, pdf_mode=True))
    edge_candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    browser = next((candidate for candidate in edge_candidates if candidate.exists()), None)
    if browser is None:
        return "ไม่พบโปรแกรมสร้าง PDF ในเครื่องนี้", 503

    with tempfile.TemporaryDirectory(prefix="mood-cafe-receipt-") as temp_dir:
        temp_path = Path(temp_dir)
        html_path = temp_path / "receipt.html"
        pdf_path = temp_path / "receipt.pdf"
        html_path.write_text(html, encoding="utf-8")
        subprocess.run(
            [str(browser), "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--user-data-dir={temp_path / 'profile'}",
             f"--print-to-pdf={pdf_path}", html_path.as_uri()],
            check=True, capture_output=True, timeout=30
        )
        pdf_data = pdf_path.read_bytes()

    return send_file(io.BytesIO(pdf_data), mimetype="application/pdf",
                     as_attachment=True, download_name=f"{order['receipt_number']}.pdf")
@app.route("/order-history")
def order_history():
    if not session.get("user_id"):
        return redirect(url_for("home"))

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (session.get("user_id"),)
        ).fetchall()

    orders = []
    for row in rows:
        order = dict(row)
        order["items"] = normalize_order_items(order.get("items_json"))
        orders.append(order)

    return render_template("order_history.html", orders=orders)


@app.route("/rewards", methods=["GET", "POST"])
def loyalty_rewards():
    if not session.get("user_id"):
        return redirect(url_for("login", next=url_for("loyalty_rewards")))
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    user_id = session["user_id"]
    if request.method == "GET":
        notify_pending_loyalty_emails(user_id)
    if request.method == "POST":
        reward_id = request.form.get("reward_id", type=int)
        with sqlite3.connect(DB_PATH) as conn:
            reward = conn.execute(
                "SELECT id FROM loyalty_rewards WHERE id=? AND user_id=? AND redeemed_order_id IS NULL",
                (reward_id, user_id),
            ).fetchone() if reward_id else None
        if reward:
            session["claimed_loyalty_reward_id"] = reward[0]
            return redirect(url_for("loyalty_reward_menu"))
        flash("ไม่พบสิทธิ์ที่ยังใช้ได้ กรุณาตรวจสอบสิทธิ์ของคุณอีกครั้งค่ะ", "error")
        return redirect(url_for("loyalty_rewards"))

    claimed_reward_id = session.get("claimed_loyalty_reward_id")
    claimed_reward = None
    if claimed_reward_id:
        with sqlite3.connect(DB_PATH) as conn:
            claimed_reward = conn.execute(
                "SELECT id FROM loyalty_rewards WHERE id=? AND user_id=? AND redeemed_order_id IS NULL",
                (claimed_reward_id, user_id),
            ).fetchone()
        if not claimed_reward:
            session.pop("claimed_loyalty_reward_id", None)
    return render_template(
        "loyalty_rewards.html", loyalty=get_loyalty_summary(user_id),
        claimed_reward=claimed_reward,
    )


@app.route("/rewards/select", methods=["GET", "POST"])
def loyalty_reward_menu():
    if not session.get("user_id"):
        return redirect(url_for("login", next=url_for("loyalty_rewards")))
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    user_id = session["user_id"]
    reward_id = session.get("claimed_loyalty_reward_id")
    with sqlite3.connect(DB_PATH) as conn:
        reward = conn.execute(
            "SELECT id FROM loyalty_rewards WHERE id=? AND user_id=? AND redeemed_order_id IS NULL",
            (reward_id, user_id),
        ).fetchone() if reward_id else None
    if not reward:
        session.pop("claimed_loyalty_reward_id", None)
        flash("กดยืนยันรับสิทธิ์ก่อนเลือกเมนูนะคะ", "error")
        return redirect(url_for("loyalty_rewards"))

    error = ""
    if request.method == "POST":
        try:
            item_id = int(request.form.get("item_id", ""))
            style_code = request.form.get("style", "")
            sweetness = request.form.get("sweetness", "50")
            note = request.form.get("note", "").strip()[:240]
            ice_separate = request.form.get("ice_separate") == "yes" and style_code == "iced"
            if style_code not in STYLE_NAMES or sweetness not in SWEETNESS_OPTIONS:
                raise ValueError("กรุณาเลือกรูปแบบเครื่องดื่มให้ถูกต้อง")
            drink_item = next((item for item in get_drinks(active_only=True) if item["id"] == item_id), None)
            if not drink_item or style_code not in drink_item["prices"]:
                raise ValueError("ไม่พบเมนูที่เลือก กรุณาลองใหม่ค่ะ")
            price = drink_item["prices"][style_code]
            if price > LOYALTY_REWARD_PRICE_LIMIT:
                raise ValueError("สิทธิ์นี้ใช้ได้กับเมนูที่ราคารวม VAT ไม่เกิน 100 บาทค่ะ")
            tracking_code = "MC-" + secrets.token_hex(4).upper()
            item = {
                "name": drink_item["name"], "price": price, "quantity": 1,
                "style_name": STYLE_NAMES[style_code], "sweet": f"ความหวาน {sweetness}%",
                "ice_separate": ice_separate,
            }
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.execute(
                    """INSERT INTO orders
                       (user_id, created_at, mood, note, items_json, total, status, tracking_code,
                        payment_status, payment_method, discount_amount, loyalty_reward_id, loyalty_processed)
                       VALUES (?, ?, ?, ?, ?, 0, 'รับออร์เดอร์แล้วค่ะ', ?, 'paid', 'loyalty_reward', ?, ?, 1)""",
                    (user_id, created_at, "แลกสิทธิ์สะสมแต้ม", note,
                     json.dumps([item], ensure_ascii=False), tracking_code, price, reward[0]),
                )
                order_id = cursor.lastrowid
                claimed = conn.execute(
                    "UPDATE loyalty_rewards SET redeemed_order_id=? WHERE id=? AND user_id=? AND redeemed_order_id IS NULL",
                    (order_id, reward[0], user_id),
                )
                if claimed.rowcount != 1:
                    raise ValueError("สิทธิ์นี้ถูกใช้ไปแล้ว กรุณาเลือกใหม่ค่ะ")
            session.pop("claimed_loyalty_reward_id", None)
            order = {
                "id": order_id, "items": [item], "mood": "แลกสิทธิ์สะสมแต้ม", "note": note,
                "total": 0, "tracking_code": tracking_code, "status": "รับออร์เดอร์แล้วค่ะ",
                "payment_status": "paid", "payment_method": "loyalty_reward",
            }
            return render_template("order.html", order=order, total=0, note=note,
                                   mood=order["mood"], tracking_code=tracking_code,
                                   loyalty_reward_order=True)
        except (ValueError, TypeError, OverflowError) as exc:
            error = str(exc) or "เลือกเมนูไม่สำเร็จ กรุณาลองใหม่ค่ะ"

    drinks = get_drinks(active_only=True)
    for item in drinks:
        item["eligible_prices"] = {
            code: price for code, price in item["prices"].items()
            if price <= LOYALTY_REWARD_PRICE_LIMIT
        }
        item["minimum_price"] = min(item["prices"].values()) if item["prices"] else 0
    return render_template(
        "loyalty_reward_menu.html", drinks=drinks, styles=STYLE_NAMES,
        sweetness_options=SWEETNESS_OPTIONS, error=error,
    )


@app.route("/track", methods=["GET", "POST"])
def track_order():
    if not session.get("user_id"):
        return redirect(url_for("home"))

    order = None
    message = ""

    tracking_code = request.args.get("code", "").strip().upper()
    if request.method == "POST":
        tracking_code = request.form.get("tracking_code", "").strip().upper()

    if tracking_code:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM orders
                WHERE tracking_code = ? AND user_id = ?
                """,
                (tracking_code, session.get("user_id"))
            ).fetchone()
        if row:
            order = dict(row)
            order["items"] = normalize_order_items(order.get("items_json"))
        else:
            message = "ไม่พบรหัสออร์เดอร์ กรุณาตรวจสอบอีกครั้ง"

    return render_template("track.html", order=order, message=message)


@app.route("/api/track/<tracking_code>")
def track_order_status_api(tracking_code):
    if not session.get("user_id"):
        return jsonify({"error": "กรุณาเข้าสู่ระบบ"}), 401
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, payment_status, payment_slip_path FROM orders WHERE tracking_code = ? AND user_id = ?",
            (tracking_code.strip().upper(), session.get("user_id"))
        ).fetchone()
    if not row:
        return jsonify({"error": "ไม่พบออร์เดอร์"}), 404
    response = jsonify({
        "status": row["status"],
        "payment_status": row["payment_status"],
        "payment_slip_submitted": bool(row["payment_slip_path"]),
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def get_admin_orders_signature():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT id, created_at, status, payment_status, payment_slip_path,
                      total, items_json, note FROM orders ORDER BY id"""
        ).fetchall()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@app.route("/api/admin/orders-version")
def admin_orders_version():
    if not session.get("is_admin"):
        return jsonify({"error": "forbidden"}), 403
    response = jsonify({"signature": get_admin_orders_signature()})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

def get_admin_sales_summary():
    now = datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    month_start = day_start.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    year_start = day_start.replace(month=1, day=1)
    next_year = year_start.replace(year=year_start.year + 1)

    def paid_sales_between(conn, start, end):
        return conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM orders "
            "WHERE payment_status = 'paid' AND created_at >= ? AND created_at < ?",
            (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchone()[0]

    with sqlite3.connect(DB_PATH) as conn:
        summary = {
            "order_count": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "revenue": conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE payment_status = 'paid'").fetchone()[0],
            "sales_today": paid_sales_between(conn, day_start, day_start + timedelta(days=1)),
            "sales_week": paid_sales_between(conn, week_start, week_start + timedelta(days=7)),
            "sales_month": paid_sales_between(conn, month_start, next_month),
            "sales_year": paid_sales_between(conn, year_start, next_year),
            "pending_order_count": conn.execute("SELECT COUNT(*) FROM orders WHERE payment_status = 'pending'").fetchone()[0],
            "customer_count": conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 0 AND email_verified = 1").fetchone()[0],
        }
        summary["active_count"] = conn.execute("SELECT COUNT(*) FROM menu_items WHERE active=1").fetchone()[0]
        summary["menu_count"] = conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0]
    return summary


@app.route("/admin")
def admin_dashboard():
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    return render_template("admin.html", **get_admin_sales_summary(), active_admin_page="overview")


def admin_only():
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    return None


@app.route("/admin/sales")
def admin_sales():
    denied = admin_only()
    if denied:
        return denied
    period = request.args.get("period", "day")
    if period not in {"day", "month", "year"}:
        period = "day"
    today = datetime.now()
    value = request.args.get("value", "")
    try:
        if period == "day":
            start = datetime.strptime(value, "%Y-%m-%d") if value else today.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            value = start.strftime("%Y-%m-%d")
            period_label = start.strftime("%d/%m/%Y")
        elif period == "month":
            start = datetime.strptime(value + "-01", "%Y-%m-%d") if value else today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            value = start.strftime("%Y-%m")
            period_label = start.strftime("%m/%Y")
        else:
            year = int(value) if value else today.year
            if year < 2000 or year > today.year + 1:
                raise ValueError
            start = datetime(year, 1, 1)
            end = datetime(year + 1, 1, 1)
            value = str(year)
            period_label = value
    except (ValueError, TypeError):
        return redirect(url_for("admin_sales", period=period))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT orders.*, COALESCE(orders.customer_name, users.username, 'ลูกค้าหน้าร้าน') AS display_customer
               FROM orders LEFT JOIN users ON users.id = orders.user_id
               WHERE orders.payment_status = 'paid' AND COALESCE(orders.payment_method, '') != 'loyalty_reward'
                 AND orders.created_at >= ? AND orders.created_at < ?
               ORDER BY orders.created_at DESC, orders.id DESC""",
            (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
    sales = [dict(row) for row in rows]
    return render_template(
        "admin_sales.html", period=period, value=value, period_label=period_label,
        sales=sales, sales_total=sum(float(row.get("total") or 0) for row in sales),
        active_admin_page="sales", pending_order_count=get_admin_sales_summary()["pending_order_count"],
    )


@app.route("/admin/customers")
def admin_customers():
    denied = admin_only()
    if denied:
        return denied
    selected_user = request.args.get("user", type=int)
    selected_phone = request.args.get("phone", "").strip()
    customers = []
    detail = None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        members = conn.execute(
            """SELECT u.id, u.username, u.email, u.phone, u.created_at,
                      COALESCE(u.loyalty_stamps,0) AS loyalty_stamps,
                      (SELECT COUNT(*) FROM loyalty_rewards r WHERE r.user_id=u.id AND r.redeemed_order_id IS NULL) AS rewards_available,
                      COUNT(o.id) AS order_count,
                      COALESCE(SUM(CASE WHEN o.payment_status='paid' THEN o.total ELSE 0 END),0) AS spent,
                      MAX(o.created_at) AS last_order
               FROM users u LEFT JOIN orders o ON o.user_id=u.id
               WHERE u.is_admin=0 AND u.email_verified=1 GROUP BY u.id ORDER BY last_order DESC, u.id DESC"""
        ).fetchall()
        for row in members:
            customers.append({**dict(row), "kind": "สมาชิก", "key": str(row["id"])})
        guests = conn.execute(
            """SELECT customer_phone AS phone, MAX(customer_name) AS username,
                      COUNT(*) AS order_count,
                      COALESCE(SUM(CASE WHEN payment_status='paid' THEN total ELSE 0 END),0) AS spent,
                      MAX(created_at) AS last_order
               FROM orders WHERE user_id IS NULL AND customer_phone IS NOT NULL AND customer_phone != ''
               GROUP BY customer_phone ORDER BY last_order DESC"""
        ).fetchall()
        for row in guests:
            customers.append({**dict(row), "id": None, "email": "", "created_at": None,
                              "loyalty_stamps": 0, "rewards_available": 0,
                              "kind": "ลูกค้าหน้าร้าน", "key": row["phone"]})
        customers.sort(key=lambda customer: customer.get("last_order") or "", reverse=True)
        if selected_user:
            customer_row = conn.execute("""SELECT id, username, email, phone, created_at, loyalty_stamps,
                (SELECT COUNT(*) FROM loyalty_rewards r WHERE r.user_id=users.id AND r.redeemed_order_id IS NULL) AS rewards_available
                FROM users WHERE id=? AND is_admin=0 AND email_verified=1""", (selected_user,)).fetchone()
            if customer_row:
                detail = dict(customer_row)
                history_rows = conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC, id DESC", (selected_user,)).fetchall()
        elif selected_phone:
            customer_row = conn.execute(
                "SELECT customer_name AS username, customer_phone AS phone FROM orders WHERE customer_phone=? AND user_id IS NULL ORDER BY id DESC LIMIT 1",
                (selected_phone,),
            ).fetchone()
            if customer_row:
                detail = {**dict(customer_row), "email": "", "created_at": None,
                          "loyalty_stamps": 0, "rewards_available": 0}
                history_rows = conn.execute("SELECT * FROM orders WHERE customer_phone=? AND user_id IS NULL ORDER BY created_at DESC, id DESC", (selected_phone,)).fetchall()
        if detail:
            detail["orders"] = []
            for history_row in history_rows:
                order = dict(history_row)
                order["items"] = normalize_order_items(order.get("items_json"))
                detail["orders"].append(order)
    return render_template("admin_customers.html", customers=customers, detail=detail,
                           active_admin_page="customers", pending_order_count=get_admin_sales_summary()["pending_order_count"])


@app.route("/admin/menu")
def admin_menu():
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    menu_items = get_drinks(active_only=False, include_vat=False)
    for item in menu_items:
        item["sale_prices"] = {code: price_including_vat(price) for code, price in item["prices"].items()}
    return render_template("admin_menu.html", menu_items=menu_items,
                           categories=CATEGORY_NAMES, styles=STYLE_NAMES,
                           moods=MOOD_NAMES, notice=session.pop("admin_notice", ""),
                           active_admin_page="menu", pending_order_count=get_admin_sales_summary()["pending_order_count"])


@app.route("/admin/orders")
def admin_orders():
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    orders = []
    for row in rows:
        order = dict(row)
        order["items"] = normalize_order_items(order.get("items_json"))
        orders.append(order)
    return render_template("orders.html", orders=orders,
                           notice=session.pop("admin_notice", ""),
                           active_admin_page="orders",
                           pending_order_count=get_admin_sales_summary()["pending_order_count"],
                           admin_orders_signature=get_admin_orders_signature())


def admin_menu_values(form):
    name = form.get("name", "").strip()
    category = form.get("category", "")
    emoji = ""
    if not name or len(name) > 80 or category not in CATEGORY_NAMES :
        raise ValueError("กรุณากรอกชื่อ หมวดหมู่ และสัญลักษณ์ให้ถูกต้อง")
    prices = {}
    for code in STYLE_NAMES:
        raw = form.get(f"price_{code}", "").strip()
        if raw:
            try:
                price = int(raw)
            except ValueError:
                raise ValueError("ราคาต้องเป็นตัวเลขจำนวนเต็ม")
            if price < 1 or price > 5000:
                raise ValueError("ราคาต้องอยู่ระหว่าง 1–5,000 บาท")
            prices[code] = price
    if not prices:
        raise ValueError("กรุณากำหนดราคาอย่างน้อยหนึ่งรูปแบบ")
    allowed_moods = set(MOOD_NAMES)
    moods = [value for value in form.getlist("moods") if value in allowed_moods]
    if not moods:
        moods = list(MOOD_NAMES)
    return name, category, emoji, prices, moods


def save_menu_image(upload):
    if not upload or not upload.filename:
        return None
    data = upload.read(MAX_MENU_IMAGE_BYTES + 1)
    if len(data) > MAX_MENU_IMAGE_BYTES:
        raise ValueError("รูปเมนูต้องมีขนาดไม่เกิน 5 MB")
    signatures = {
        "jpg": (data.startswith(b"\xff\xd8\xff"), "jpg"),
        "png": (data.startswith(b"\x89PNG\r\n\x1a\n"), "png"),
        "webp": (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP", "webp"),
    }
    detected = next((extension for valid, extension in signatures.values() if valid), None)
    if not detected:
        raise ValueError("รองรับรูปภาพ JPG, PNG หรือ WEBP เท่านั้น")
    original = secure_filename(upload.filename)
    stem = Path(original).stem[:40] or "menu"
    filename = f"{stem}-{secrets.token_hex(6)}.{detected}"
    MENU_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (MENU_UPLOAD_DIR / filename).write_bytes(data)
    return filename


def remove_uploaded_menu_image(filename):
    if not filename:
        return
    path = (MENU_UPLOAD_DIR / Path(filename).name).resolve()
    if path.parent == MENU_UPLOAD_DIR.resolve() and path.is_file():
        try:
            path.unlink()
        except PermissionError:
            # Windows may briefly lock an image while a browser response is streaming.
            # The file is no longer referenced by the menu; a later cleanup can remove it.
            pass


@app.route("/admin/menu/add", methods=["POST"])
def admin_add_menu_item():
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    uploaded = None
    try:
        name, category, emoji, prices, moods = admin_menu_values(request.form)
        uploaded = save_menu_image(request.files.get("image"))
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO menu_items (name,category,emoji,prices_json,moods_json,active,updated_at,image_filename) VALUES (?,?,?,?,?,1,?,?)",
                         (name, category, emoji, json.dumps(prices), json.dumps(moods), datetime.now().isoformat(timespec="seconds"), uploaded))
        session["admin_notice"] = "เพิ่มเมนูเรียบร้อยแล้ว"
    except (ValueError, sqlite3.IntegrityError) as exc:
        if uploaded:
            remove_uploaded_menu_image(uploaded)
        session["admin_notice"] = str(exc) if isinstance(exc, ValueError) else "มีเมนูชื่อนี้อยู่แล้ว"
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/<int:item_id>/update", methods=["POST"])
def admin_update_menu_item(item_id):
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    uploaded = None
    old_image = None
    try:
        name, category, emoji, prices, moods = admin_menu_values(request.form)
        uploaded = save_menu_image(request.files.get("image"))
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            current = conn.execute("SELECT image_filename FROM menu_items WHERE id=?", (item_id,)).fetchone()
            if not current:
                raise ValueError("ไม่พบเมนูที่ต้องการแก้ไข")
            old_image = current["image_filename"]
            conn.execute("UPDATE menu_items SET name=?,category=?,emoji=?,prices_json=?,moods_json=?,updated_at=?,image_filename=COALESCE(?,image_filename) WHERE id=?",
                         (name, category, emoji, json.dumps(prices), json.dumps(moods), datetime.now().isoformat(timespec="seconds"), uploaded, item_id))
        if uploaded and old_image:
            remove_uploaded_menu_image(old_image)
        session["admin_notice"] = "บันทึกการแก้ไขเมนูแล้ว"
    except (ValueError, sqlite3.IntegrityError) as exc:
        if uploaded:
            remove_uploaded_menu_image(uploaded)
        session["admin_notice"] = str(exc) if isinstance(exc, ValueError) else "มีเมนูชื่อนี้อยู่แล้ว"
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/<int:item_id>/toggle", methods=["POST"])
def admin_toggle_menu_item(item_id):
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("UPDATE menu_items SET active=1-active,updated_at=? WHERE id=?",
                              (datetime.now().isoformat(timespec="seconds"), item_id))
    if not cursor.rowcount:
        abort(404)
    session["admin_notice"] = "เปลี่ยนสถานะเมนูแล้ว"
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/<int:item_id>/delete", methods=["POST"])
def admin_delete_menu_item(item_id):
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT image_filename FROM menu_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            abort(404)
        conn.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    remove_uploaded_menu_image(row["image_filename"])
    session["admin_notice"] = "ลบเมนูออกจากระบบแล้ว"
    return redirect(url_for("admin_menu"))


@app.route("/admin/payment-slips/<path:filename>")
def admin_payment_slip(filename):
    if not session.get("is_admin"):
        abort(404)
    return render_template(
        "admin_slip.html",
        filename=filename,
        image_url=url_for("admin_payment_slip_image", filename=filename),
    )


@app.route("/admin/payment-slip-image/<path:filename>")
def admin_payment_slip_image(filename):
    if not session.get("is_admin"):
        abort(404)
    return send_from_directory(PAYMENT_SLIP_DIR, filename, as_attachment=False)


def award_loyalty_for_order(order_id):
    reward_ids = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        order = conn.execute(
            "SELECT user_id, items_json, payment_status, payment_method, loyalty_processed FROM orders WHERE id=?",
            (order_id,),
        ).fetchone()
        if not order or order["payment_status"] != "paid" or order["loyalty_processed"]:
            return 0
        if not order["user_id"] or order["payment_method"] == "loyalty_reward":
            conn.execute("UPDATE orders SET loyalty_processed=1 WHERE id=?", (order_id,))
            return 0
        quantity = sum(item.get("quantity", 0) for item in normalize_order_items(order["items_json"]))
        user = conn.execute("SELECT loyalty_stamps FROM users WHERE id=?", (order["user_id"],)).fetchone()
        if not user:
            conn.execute("UPDATE orders SET loyalty_processed=1 WHERE id=?", (order_id,))
            return 0
        total_stamps = int(user["loyalty_stamps"] or 0) + quantity
        reward_count, remaining_stamps = divmod(total_stamps, LOYALTY_STAMPS_PER_REWARD)
        conn.execute("UPDATE users SET loyalty_stamps=? WHERE id=?", (remaining_stamps, order["user_id"]))
        conn.execute("UPDATE orders SET loyalty_processed=1 WHERE id=?", (order_id,))
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for _ in range(reward_count):
            cursor = conn.execute(
                "INSERT INTO loyalty_rewards (user_id, earned_order_id, created_at) VALUES (?, ?, ?)",
                (order["user_id"], order_id, created_at),
            )
            reward_ids.append(cursor.lastrowid)
    if reward_ids:
        notify_pending_loyalty_emails(order["user_id"], reward_ids)
    return len(reward_ids)


@app.route("/admin/orders/<int:order_id>/payment", methods=["POST"])
def admin_review_payment(order_id):
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    decision = request.form.get("decision")
    if decision not in {"paid", "rejected"}:
        return "สถานะการชำระเงินไม่ถูกต้อง", 400
    should_award_loyalty = False
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        order = conn.execute("SELECT payment_status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            abort(404)
        if order["payment_status"] != "pending":
            session["admin_notice"] = "ออร์เดอร์นี้ผ่านการตรวจสอบแล้วค่ะ"
        elif decision == "paid":
            conn.execute("UPDATE orders SET payment_status='paid', status='รับออร์เดอร์แล้วค่ะ' WHERE id=?", (order_id,))
            session["admin_notice"] = "ยืนยันรับเงินแล้ว ออร์เดอร์พร้อมจัดเตรียมค่ะ"
            should_award_loyalty = True
        else:
            conn.execute("UPDATE orders SET payment_status='rejected', status='สลิปไม่ผ่าน กรุณาติดต่อร้าน' WHERE id=?", (order_id,))
            session["admin_notice"] = "ปฏิเสธสลิปแล้ว"
    if should_award_loyalty:
        reward_count = award_loyalty_for_order(order_id)
        if reward_count:
            session["admin_notice"] += f" บันทึกสิทธิ์แลกเครื่องดื่มฟรี {reward_count} ครั้ง (ครั้งละ 1 แก้ว) ในบัญชีลูกค้าแล้วค่ะ"
    return redirect(url_for("admin_orders"))


@app.route("/update-status/<int:order_id>", methods=["POST"])
def update_status(order_id):
    if not session.get("is_admin"):
        return redirect(url_for("home"))
    allowed_statuses = ["รับออร์เดอร์แล้วค่ะ", "กำลังเตรียมเครื่องดื่มรอแปปน้า", "พร้อมรับเครื่องดื่มแล้วค่ะ", "เสร็จสิ้น"]
    new_status = request.form.get("status", "")
    if new_status not in allowed_statuses:
        return "สถานะไม่ถูกต้อง", 400
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT payment_status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            abort(404)
        if row[0] != "paid":
            return "กรุณายืนยันการชำระเงินก่อนอัปเดตการจัดเตรียม", 409
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
    return redirect(url_for("admin_orders"))


def password_reset_hash(email, code):
    message = f"{email.lower()}:{code}".encode("utf-8")
    return hmac.new(str(app.secret_key).encode("utf-8"), message, hashlib.sha256).hexdigest()


def get_email_config():
    file_config = {}
    try:
        loaded = json.loads(EMAIL_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            file_config = loaded
    except (OSError, ValueError, TypeError):
        pass

    def setting(env_name, file_name, default=""):
        return os.environ.get(env_name) or file_config.get(file_name) or default

    return {
        "host": str(setting("MOOD_CAFE_SMTP_HOST", "smtp_host", "smtp.gmail.com")).strip(),
        "port": str(setting("MOOD_CAFE_SMTP_PORT", "smtp_port", "465")).strip(),
        "security": str(setting("MOOD_CAFE_SMTP_SECURITY", "security", "SSL/TLS")).strip().lower(),
        "username": str(setting("MOOD_CAFE_SMTP_USERNAME", "username", RESET_EMAIL)).strip(),
        "password": str(setting("MOOD_CAFE_SMTP_PASSWORD", "password")).replace(" ", "").strip(),
        "sender_name": str(setting("MOOD_CAFE_EMAIL_SENDER_NAME", "sender_name", "Mood Café")).strip(),
    }


def send_otp_email(email, code, purpose="password_reset"):
    mail_config = get_email_config()
    smtp_password = mail_config["password"]
    smtp_username = mail_config["username"]
    if not smtp_password or not smtp_username:
        app.logger.error("OTP email is not configured: missing SMTP credentials")
        return False

    title = "ยืนยันอีเมล" if purpose == "email_verification" else "กู้คืนรหัสผ่าน"
    safe_title = html_lib.escape(title)
    safe_code = html_lib.escape(str(code))
    message = EmailMessage()
    message["Subject"] = f"{title} | Mood Café"
    message["From"] = formataddr((mail_config["sender_name"], smtp_username))
    message["To"] = email
    message.set_content(str(code))
    message.add_alternative(f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:28px 12px;background:#f3eadf;font-family:Arial,'Tahoma',sans-serif;color:#38271f">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;margin:0 auto;background:#fffdf9;border:1px solid #eadbca;border-radius:20px;overflow:hidden">
<tr><td style="height:7px;background:linear-gradient(90deg,#9b5733,#d3a06f,#edd3ae)"></td></tr>
<tr><td style="padding:28px 28px 8px;text-align:center"><div style="font-size:12px;letter-spacing:3px;font-weight:700;color:#a36643">MOOD CAFÉ</div></td></tr>
<tr><td style="padding:14px 28px 30px;text-align:center"><div style="font-size:20px;font-weight:700;color:#38271f">{safe_title}</div><div style="margin-top:22px;font-size:12px;letter-spacing:2px;color:#8d796a">รหัส OTP</div><div style="display:inline-block;margin-top:9px;padding:15px 24px;border:1px solid #eadbca;border-radius:14px;background:#f8f1e8;color:#854c30;font-size:34px;font-weight:800;letter-spacing:9px">{safe_code}</div></td></tr>
<tr><td style="padding:13px 20px;border-top:1px solid #eee3d7;text-align:center;color:#9a897b;font-size:11px">MOOD CAFÉ · A LITTLE PAUSE, A BETTER MOOD</td></tr>
</table></body></html>""", subtype="html")

    try:
        host = mail_config["host"]
        port = int(mail_config["port"])
        if "ssl" in mail_config["security"]:
            smtp_connection = smtplib.SMTP_SSL(
                host, port, timeout=15, context=ssl.create_default_context()
            )
        else:
            smtp_connection = smtplib.SMTP(host, port, timeout=15)
        with smtp_connection as smtp:
            smtp.ehlo()
            if "tls" in mail_config["security"] and "ssl" not in mail_config["security"]:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        app.logger.exception("Could not send OTP email: %s", exc)
        return False


def send_loyalty_reward_email(email, username):
    mail_config = get_email_config()
    smtp_username = mail_config["username"]
    if not smtp_username or not mail_config["password"]:
        app.logger.error("Loyalty email is not configured: missing SMTP credentials")
        return False
    safe_name = html_lib.escape(username or "ลูกค้า Mood Café")
    message = EmailMessage()
    message["Subject"] = "ยินดีด้วย! คุณได้รับเครื่องดื่มฟรีจาก Mood Café"
    message["From"] = formataddr((mail_config["sender_name"], smtp_username))
    message["To"] = email
    message.set_content(
        "คุณซื้อเครื่องดื่มของร้าน Mood Café ครบ 10 แก้วแล้วค่ะ เย้ ยินดีด้วยค่ะ!\n"
        "คุณได้รับสิทธิ์เครื่องดื่มฟรี 1 แก้ว เลือกเมนูใดก็ได้ในร้านที่ราคารวมไม่เกิน 100 บาท\n"
        "หากต้องการใช้สิทธิ์ เข้าสู่เว็บไซต์ Mood Café แล้วกด ‘สะสมแต้ม’ จากนั้นกดยืนยันรับสิทธิ์และเลือกเมนูที่ต้องการได้เลยค่ะ\n"
        "ขอบคุณที่แวะมาเติมช่วงเวลาดี ๆ กับ Mood Café ค่ะ"
    )
    message.add_alternative(f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:28px 12px;background:#f3eadf;font-family:Arial,'Tahoma',sans-serif;color:#38271f">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;margin:0 auto;background:#fffdf9;border:1px solid #eadbca;border-radius:20px;overflow:hidden">
<tr><td style="height:7px;background:linear-gradient(90deg,#9b5733,#d3a06f,#edd3ae)"></td></tr>
<tr><td style="padding:28px 28px 8px;text-align:center"><div style="font-size:12px;letter-spacing:3px;font-weight:700;color:#a36643">MOOD CAFÉ</div></td></tr>
<tr><td style="padding:14px 28px 30px;text-align:center"><div style="font-size:22px;font-weight:800;color:#6e422d">เย้ ยินดีด้วยค่ะ คุณ{safe_name}!</div><p style="margin:18px 0 8px;line-height:1.8">คุณซื้อเครื่องดื่มของร้าน Mood Café ครบ 10 แก้วแล้วค่ะ<br>ได้รับสิทธิ์เครื่องดื่มฟรี 1 แก้ว<br>เลือกเมนูใดก็ได้ในร้านที่ราคารวมไม่เกิน 100 บาท</p><p style="margin:18px 0 0;line-height:1.8;color:#765b49">หากต้องการใช้สิทธิ์ เข้าสู่เว็บไซต์ Mood Café แล้วกด “สะสมแต้ม” เพื่อยืนยันสิทธิ์และเลือกเมนูที่ต้องการได้เลยค่ะ</p></td></tr>
<tr><td style="padding:13px 20px;border-top:1px solid #eee3d7;text-align:center;color:#9a897b;font-size:11px">ขอบคุณที่แวะมาเติมช่วงเวลาดี ๆ กับ Mood Café</td></tr>
</table></body></html>""", subtype="html")
    try:
        host = mail_config["host"]
        port = int(mail_config["port"])
        if "ssl" in mail_config["security"]:
            smtp_connection = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            smtp_connection = smtplib.SMTP(host, port, timeout=15)
        with smtp_connection as smtp:
            smtp.ehlo()
            if "tls" in mail_config["security"] and "ssl" not in mail_config["security"]:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(smtp_username, mail_config["password"])
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        app.logger.exception("Could not send loyalty reward email: %s", exc)
        return False


def notify_pending_loyalty_emails(user_id, reward_ids=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT email, username FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return 0
        if reward_ids:
            placeholders = ",".join("?" for _ in reward_ids)
            rewards = conn.execute(
                f"SELECT id FROM loyalty_rewards WHERE user_id=? AND notified_at IS NULL AND id IN ({placeholders}) ORDER BY id",
                (user_id, *reward_ids),
            ).fetchall()
        else:
            rewards = conn.execute(
                "SELECT id FROM loyalty_rewards WHERE user_id=? AND notified_at IS NULL ORDER BY id",
                (user_id,),
            ).fetchall()
    notified = 0
    for reward in rewards:
        if send_loyalty_reward_email(user["email"], user["username"]):
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE loyalty_rewards SET notified_at=? WHERE id=? AND notified_at IS NULL",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), reward["id"]),
                )
            notified += 1
    return notified


def send_password_reset_email(email, code):
    return send_otp_email(email, code, purpose="password_reset")


def render_password_reset(message="", email=""):
    email_configured = bool(get_email_config()["password"])
    return render_template(
        "forgot_password.html", message=message, email=email,
        email_configured=email_configured,
    )


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    message = ""
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        generic_message = "ถ้าอีเมลนี้มีบัญชีอยู่ ระบบจะส่งรหัสยืนยันให้ กรุณาตรวจกล่องจดหมายและสแปมค่ะ"
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            now = int(datetime.now().timestamp())
            with sqlite3.connect(DB_PATH) as conn:
                user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                previous = conn.execute(
                    "SELECT created_at FROM password_reset_codes WHERE email = ?", (email,)
                ).fetchone()
                if user and (not previous or now - previous[0] >= RESET_CODE_COOLDOWN_SECONDS):
                    code = f"{secrets.randbelow(1_000_000):06d}"
                    conn.execute(
                        """INSERT INTO password_reset_codes (email, code_hash, expires_at, created_at, attempts)
                           VALUES (?, ?, ?, ?, 0)
                           ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash,
                             expires_at=excluded.expires_at, created_at=excluded.created_at, attempts=0""",
                        (email, password_reset_hash(email, code), now + RESET_CODE_TTL_SECONDS, now),
                    )
                else:
                    code = None
            if code and not send_password_reset_email(email, code):
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("DELETE FROM password_reset_codes WHERE email = ?", (email,))
            message = generic_message
        else:
            message = generic_message
    return render_password_reset(message, email)


@app.route("/reset-password", methods=["POST"])
def reset_password():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")
    if len(password) < 8:
        return render_password_reset("รหัสผ่านใหม่ต้องมีอย่างน้อย 8 ตัวอักษรค่ะ", email), 400
    if password != password_confirm:
        return render_password_reset("รหัสผ่านทั้งสองช่องไม่ตรงกันค่ะ", email), 400
    if not re.fullmatch(r"\d{6}", code):
        return render_password_reset("รหัสยืนยันไม่ถูกต้องหรือหมดอายุแล้วค่ะ", email), 400

    now = int(datetime.now().timestamp())
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        reset = conn.execute(
            "SELECT * FROM password_reset_codes WHERE email = ?", (email,)
        ).fetchone()
        if not reset or reset["expires_at"] < now or reset["attempts"] >= RESET_CODE_MAX_ATTEMPTS:
            conn.execute("DELETE FROM password_reset_codes WHERE email = ?", (email,))
            return render_password_reset("รหัสยืนยันไม่ถูกต้องหรือหมดอายุแล้วค่ะ", email), 400
        if not hmac.compare_digest(reset["code_hash"], password_reset_hash(email, code)):
            attempts = reset["attempts"] + 1
            if attempts >= RESET_CODE_MAX_ATTEMPTS:
                conn.execute("DELETE FROM password_reset_codes WHERE email = ?", (email,))
                error_message = "กรอกรหัสผิดเกินจำนวนครั้งที่กำหนด กรุณาขอรหัสใหม่ค่ะ"
            else:
                conn.execute("UPDATE password_reset_codes SET attempts = ? WHERE email = ?", (attempts, email))
                error_message = "รหัสยืนยันไม่ถูกต้องหรือหมดอายุแล้วค่ะ"
            return render_password_reset(error_message, email), 400
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE email = ?",
            (generate_password_hash(password), email),
        )
        conn.execute("DELETE FROM password_reset_codes WHERE email = ?", (email,))

    return render_password_reset("เปลี่ยนรหัสผ่านเรียบร้อยแล้ว เข้าสู่ระบบด้วยรหัสผ่านใหม่ได้เลยค่ะ", email)


@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    email = request.form.get("email", request.args.get("email", "")).strip().lower()
    message = ""
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        now = int(datetime.now().timestamp())
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            verification = conn.execute(
                "SELECT * FROM email_verification_codes WHERE email = ?", (email,)
            ).fetchone()
            if not verification or verification["expires_at"] < now or verification["attempts"] >= RESET_CODE_MAX_ATTEMPTS:
                conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))
                message = "รหัส OTP ไม่ถูกต้องหรือหมดอายุแล้ว กรุณาขอรหัสใหม่ค่ะ"
            elif not re.fullmatch(r"\d{6}", code) or not hmac.compare_digest(
                verification["code_hash"], password_reset_hash(email, code)
            ):
                attempts = verification["attempts"] + 1
                if attempts >= RESET_CODE_MAX_ATTEMPTS:
                    conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))
                    message = "กรอกรหัสผิดเกินจำนวนครั้ง กรุณาขอรหัสใหม่ค่ะ"
                else:
                    conn.execute("UPDATE email_verification_codes SET attempts = ? WHERE email = ?", (attempts, email))
                    message = "รหัส OTP ไม่ถูกต้องค่ะ ลองตรวจสอบอีเมลอีกครั้ง"
            else:
                user = conn.execute("SELECT * FROM users WHERE id = ?", (verification["user_id"],)).fetchone()
                if not user:
                    conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))
                    message = "ไม่พบบัญชี กรุณาสมัครสมาชิกใหม่ค่ะ"
                else:
                    conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user["id"],))
                    conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))
                    session.clear()
                    return redirect(url_for("login", verified="1"))

    return render_template(
        "verify_email.html", email=email, message=message,
        email_configured=bool(get_email_config()["password"]),
    )


@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    email = request.form.get("email", "").strip().lower()
    now = int(datetime.now().timestamp())
    code = None
    with sqlite3.connect(DB_PATH) as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE email = ? AND email_verified = 0", (email,)
        ).fetchone()
        previous = conn.execute(
            "SELECT created_at FROM email_verification_codes WHERE email = ?", (email,)
        ).fetchone()
        if user and (not previous or now - previous[0] >= RESET_CODE_COOLDOWN_SECONDS):
            code = f"{secrets.randbelow(1_000_000):06d}"
            conn.execute(
                """INSERT INTO email_verification_codes (email,user_id,code_hash,expires_at,created_at,attempts)
                   VALUES (?,?,?,?,?,0)
                   ON CONFLICT(email) DO UPDATE SET user_id=excluded.user_id,code_hash=excluded.code_hash,
                     expires_at=excluded.expires_at,created_at=excluded.created_at,attempts=0""",
                (email, user[0], password_reset_hash(email, code), now + RESET_CODE_TTL_SECONDS, now),
            )
    if code and not send_otp_email(email, code, purpose="email_verification"):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))
    return render_template(
        "verify_email.html", email=email,
        message="ถ้าบัญชีนี้ยังไม่ได้ยืนยัน ระบบจะส่ง OTP ให้ กรุณาตรวจกล่องจดหมายและสแปมค่ะ",
        email_configured=bool(get_email_config()["password"]),
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    message = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = re.sub(r"[\s-]", "", request.form.get("phone", ""))
        password = request.form.get("password", "")

        if not username or not email or not phone or not password:
            message = "กรุณากรอกข้อมูลให้ครบทุกช่อง"
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            message = "กรุณากรอกอีเมลให้ถูกต้อง"
        elif not re.fullmatch(r"(?:0\d{8,9}|\+66\d{8,9})", phone):
            message = "กรุณากรอกเบอร์โทรศัพท์ให้ถูกต้อง"
        elif len(password) < 8:
            message = "รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร"
        else:
            try:
                password_hash = generate_password_hash(password)
                now = int(datetime.now().timestamp())
                code = f"{secrets.randbelow(1_000_000):06d}"
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.execute(
                        """INSERT INTO users
                           (username,email,phone,password_hash,created_at,is_admin,email_verified)
                           VALUES (?,?,?,?,?,0,0)""",
                        (username,email,phone,password_hash,datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    )
                    user_id = cursor.lastrowid
                    conn.execute(
                        """INSERT INTO email_verification_codes
                           (email,user_id,code_hash,expires_at,created_at,attempts)
                           VALUES (?,?,?,?,?,0)""",
                        (email,user_id,password_reset_hash(email,code),now+RESET_CODE_TTL_SECONDS,now),
                    )
                sent = send_otp_email(email,code,purpose="email_verification")
                if not sent:
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("DELETE FROM email_verification_codes WHERE email = ?",(email,))
                verify_url = url_for("verify_email",email=email)
                notice = (
                    "ส่งรหัส OTP ไปยังอีเมลแล้วค่ะ กรุณากรอกรหัสเพื่อยืนยันบัญชี"
                    if sent else
                    "สร้างบัญชีแล้ว แต่ส่ง OTP ไม่สำเร็จ ตรวจสอบการตั้งค่าอีเมลแล้วกดส่งรหัสอีกครั้งค่ะ"
                )
                if is_ajax:
                    return jsonify({"success": True, "redirect": verify_url})
                return render_template("verify_email.html",email=email,message=notice,
                                       email_configured=bool(get_email_config()["password"]))
            except sqlite3.IntegrityError:
                message = "ชื่อผู้ใช้หรืออีเมลนี้ถูกใช้แล้ว"

        if is_ajax:
            return jsonify({"success": False,"message": message}),400

    return render_template("register.html",message=message)


@app.route("/login", methods=["GET", "POST"])
def login():
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    message = "ยืนยันอีเมลสำเร็จแล้ว กรุณาเข้าสู่ระบบค่ะ" if request.args.get("verified") == "1" else ""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            if not user["email_verified"]:
                message = "กรุณายืนยันอีเมลก่อนเข้าสู่ระบบค่ะ"
                if is_ajax:
                    return jsonify({"success": False, "message": message,
                                    "redirect": url_for("verify_email", email=email)}), 403
                return render_template("login.html", message=message, verification_email=email)
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["is_admin"] = user["is_admin"]

            if is_ajax:
                return jsonify({"success": True, "redirect": url_for("choose")})

            return redirect(url_for("choose"))

        message = "อีเมลหรือรหัสผ่านไม่ถูกต้อง"

        if is_ajax:
            return jsonify({"success": False, "message": message}), 400

    return render_template("login.html", message=message, verification_email="")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "POST":
        session.clear()
        return redirect(url_for("home"))
    return redirect(url_for("home"))


if __name__ == "__main__":
    debug_enabled = os.environ.get("MOOD_CAFE_DEBUG", "").lower() in {"1", "true", "yes"}
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5001")), debug=debug_enabled)









