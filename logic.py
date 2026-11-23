import math
import re
import secrets

DEFAULT_THRESHOLD = 3
SAFETY_DAYS = 2
MAX_INT = 2147483647  # mysql INT max

INT_FIELDS = ["stock", "lead_time_days", "expiry_days", "reorder_level"]
REQUIRED_FIELDS = ["sku", "name", "stock", "avg_daily_sales",
                   "lead_time_days", "expiry_days", "reorder_level"]


def fmt(x):
    if x == math.inf:
        return "unlimited"
    x = float(x)
    if x.is_integer():
        return str(int(x))
    return f"{x:.1f}"


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _ceil(x):
    # round first so 25.000000001 doesn't become 26
    return math.ceil(round(x, 6))


def is_low_stock(stock, reorder_level):
    return stock <= reorder_level


def is_near_expiry(expiry_days, threshold=DEFAULT_THRESHOLD):
    return expiry_days <= threshold


def is_expired(expiry_days):
    return expiry_days <= 0


def days_of_cover(stock, avg_daily_sales):
    if avg_daily_sales <= 0:
        return math.inf  # nothing sells, so it never runs out
    return stock / avg_daily_sales


def reorder_qty(stock, avg_daily_sales, lead_time_days):
    return max(0, _ceil(avg_daily_sales * (lead_time_days + SAFETY_DAYS) - stock))


def reorder_calc_text(stock, avg_daily_sales, lead_time_days):
    qty = reorder_qty(stock, avg_daily_sales, lead_time_days)
    return (f"max(0, ceil({fmt(avg_daily_sales)} x ({lead_time_days} + {SAFETY_DAYS})"
            f" - {stock})) = {qty}")


def analyze_product(p, threshold=DEFAULT_THRESHOLD):
    stock = int(p["stock"])
    sales = float(p["avg_daily_sales"])
    lead = int(p["lead_time_days"])
    expiry = int(p["expiry_days"])
    level = int(p["reorder_level"])

    low = is_low_stock(stock, level)
    near = is_near_expiry(expiry, threshold)
    expired = is_expired(expiry)
    cover = days_of_cover(stock, sales)
    qty = reorder_qty(stock, sales, lead)

    flags = []
    if low:
        flags.append("LOW STOCK")
    if near:
        flags.append("EXPIRED" if expired else "NEAR EXPIRY")

    # units that probably won't sell before expiry
    at_risk = max(0, _ceil(stock - sales * max(expiry, 0)))

    parts = []
    if low:
        parts.append(f"{stock} left, sells {fmt(sales)}/day, lasts {fmt(cover)} days")
    if expired:
        parts.append("this batch has expired")
    elif near:
        text = f"expires in {plural(expiry, 'day')}"
        if not low:
            text += f", {stock} in stock, ~{at_risk} may not sell in time"
        parts.append(text)
    reason = "; ".join(parts)
    if reason:
        reason = reason[0].upper() + reason[1:]

    if low:
        reorder_part = f"Reorder {qty} units" if qty > 0 else "Monitor stock (reorder qty is 0)"
    if expired:
        action = "Remove from shelf"
        if low and qty > 0:
            action += f" + reorder {qty} units"
    elif low and near:
        action = reorder_part + " + sell old batch first"
    elif low:
        action = reorder_part
    elif near:
        action = "Sell first / discount"  # no reorder for expiry-only items
    else:
        action = ""

    return {
        "sku": p["sku"],
        "name": p["name"],
        "stock": stock,
        "avg_daily_sales": sales,
        "lead_time_days": lead,
        "expiry_days": expiry,
        "reorder_level": level,
        "low_stock": low,
        "near_expiry": near,
        "expired": expired,
        "flagged": low or near,
        "flags": flags,
        "status": "expired" if expired else "low" if low else "expiring" if near else "ok",
        "at_risk_units": at_risk if near else 0,
        # json has no infinity, so None = never runs out
        "days_of_cover": None if cover == math.inf else round(cover, 2),
        "reorder_qty": qty if low else 0,
        "reorder_calc": reorder_calc_text(stock, sales, lead),
        "reason": reason,
        "action": action,
    }


def _cover_key(item):
    return math.inf if item["days_of_cover"] is None else item["days_of_cover"]


def prioritize(analyzed):
    # low stock first (least cover), then expiry only (fewest days), then the rest
    low = sorted([a for a in analyzed if a["low_stock"]], key=_cover_key)
    expiry_only = sorted([a for a in analyzed if a["near_expiry"] and not a["low_stock"]],
                         key=lambda a: a["expiry_days"])
    rest = [a for a in analyzed if not a["flagged"]]
    return low + expiry_only + rest


def count_false_alarms(analyzed, threshold=DEFAULT_THRESHOLD):
    # recheck every flag against the raw numbers, should always be 0
    n = 0
    for a in analyzed:
        if "LOW STOCK" in a["flags"] and not a["stock"] <= a["reorder_level"]:
            n += 1
        if a["near_expiry"] and not a["expiry_days"] <= threshold:
            n += 1
        if "Reorder" in a["action"] and not a["low_stock"]:
            n += 1
        if "discount" in a["action"] and not a["near_expiry"]:
            n += 1
    return n


def compute_metrics(analyzed, threshold=DEFAULT_THRESHOLD):
    low_flags = sum(1 for a in analyzed if a["low_stock"])
    expiry_flags = sum(1 for a in analyzed if a["near_expiry"])
    return {
        "total_flags": low_flags + expiry_flags,
        "products_flagged": sum(1 for a in analyzed if a["flagged"]),
        "baseline_flags": low_flags,
        "low_stock_flags": low_flags,
        "near_expiry_flags": expiry_flags,
        "false_alarms": count_false_alarms(analyzed, threshold),
        "total_products": len(analyzed),
    }


SLOW_SELLER_DAYS = 30
LEVEL_ORDER = {"urgent": 0, "warning": 1, "tip": 2}


def _suggestion(level, kind, a, title, detail):
    return {"level": level, "kind": kind, "sku": a["sku"], "name": a["name"],
            "title": title, "detail": detail}


def _days(n):
    return f"{fmt(n)} day" + ("" if n == 1 else "s")


def build_insights(analyzed):
    tips = []
    for a in analyzed:
        name, stock, sales = a["name"], a["stock"], a["avg_daily_sales"]
        lead, level, expiry = a["lead_time_days"], a["reorder_level"], a["expiry_days"]
        cover = _cover_key(a)

        def add(lvl, kind, title, detail):
            tips.append(_suggestion(lvl, kind, a, title, detail))

        if a["expired"]:
            add("urgent", "remove", f"Remove {name} from the shelf",
                f"This batch has expired. Take all {stock} off the shelf so no customer buys it.")
        if a["low_stock"] and a["reorder_qty"] > 0:
            if cover < lead:
                add("urgent", "order", f"Order {name} today",
                    f"Only {stock} left, enough for about {_days(cover)}, but delivery takes "
                    f"{_days(lead)}. Order {a['reorder_qty']} now to avoid running out.")
            else:
                add("warning", "order", f"Order {name} soon",
                    f"{stock} left, enough for about {_days(cover)}. Order {a['reorder_qty']}.")

        if a["near_expiry"] and not a["expired"] and a["at_risk_units"] > 0:
            add("warning", "discount", f"Put {name} on offer",
                f"About {a['at_risk_units']} of {stock} may not sell before it expires in "
                f"{_days(expiry)}. Offer a discount or place it at the front.")
        elif not a["near_expiry"] and sales > 0 and cover > expiry:
            add("warning", "overstock", f"Too much {name} in stock",
                f"At today's sales, {stock} will take about {_days(cover)} to sell, but it "
                f"expires in {_days(expiry)}. Order less next time.")

        if sales > 0 and level < sales * lead:
            better = _ceil(sales * (lead + 1))
            add("tip", "reorder_level", f"Order {name} earlier",
                f"When stock reaches {level}, it lasts only {_days(level / sales)}, but delivery "
                f"takes {_days(lead)}. Start ordering when you reach {better} instead.")
        if sales == 0 and stock > 0:
            add("tip", "not_selling", f"{name} is not selling",
                "No sales recorded. Avoid ordering more until it starts selling.")
        elif not a["flagged"] and cover > SLOW_SELLER_DAYS and cover <= expiry:
            add("tip", "slow", f"{name} sells slowly",
                f"Current stock lasts about {_days(round(cover))}. You can keep less and "
                f"free up money and shelf space.")

    tips.sort(key=lambda t: LEVEL_ORDER[t["level"]])

    statuses = [a["status"] for a in analyzed]
    health = {s: statuses.count(s) for s in ("expired", "low", "expiring", "ok")}
    health["total"] = len(analyzed)

    best = sorted((a for a in analyzed if a["avg_daily_sales"] > 0),
                  key=lambda a: -a["avg_daily_sales"])[:3]
    return {
        "health": health,
        "suggestions": tips,
        "best_sellers": [{"sku": a["sku"], "name": a["name"], "per_day": a["avg_daily_sales"]}
                         for a in best],
    }


def analyze_inventory(products, threshold=DEFAULT_THRESHOLD):
    analyzed = [analyze_product(p, threshold) for p in products]
    priority = prioritize(analyzed)
    purchase_list = [
        {"sku": a["sku"], "name": a["name"], "qty": a["reorder_qty"], "calc": a["reorder_calc"]}
        for a in priority if a["low_stock"] and a["reorder_qty"] > 0
    ]
    return {
        "threshold": threshold,
        "products": sorted(analyzed, key=lambda a: a["sku"]),
        "priority": priority,
        "purchase_list": purchase_list,
        "metrics": compute_metrics(analyzed, threshold),
        "insights": build_insights(analyzed),
    }


# assistant: just keyword matching, no AI model

EXAMPLE_QUESTIONS = [
    "Which items are most urgent today?",
    "Which products need reordering?",
    "Which products are nearing expiry?",
    "What are my best sellers?",
    "Which products are moving slowly?",
    "Give me a summary",
]

# checked in this order, first match wins
INTENTS = [
    ("expiring", ("expire", "expiry", "expiring", "nearing expiry", "waste", "spoil", "old stock", "fresh")),
    ("order", ("order", "reorder", "replenish", "buy", "purchase", "supplier", "restock",
               "run out", "running out", "low")),
    ("discount", ("discount", "offer", "sale")),
    ("best", ("best", "top seller", "top selling", "popular", "fast", "sells most", "most sold")),
    ("slow", ("slow", "not selling", "too much", "overstock")),
    ("tasks", ("first", "today", "urgent", "priority", "priorities", "should i do", "to do", "todo",
               "attention", "help me")),
    ("summary", ("summary", "health", "overview", "how is my shop", "how's my shop", "status", "report")),
]


def _item(title, detail, level="info", sku=None):
    return {"title": title, "detail": detail, "level": level, "sku": sku}


def _level(a):
    return "urgent" if a["low_stock"] or a["expired"] else "warning" if a["near_expiry"] else "ok"


def _about_product(a):
    cover = "forever (no sales)" if a["days_of_cover"] is None else _days(a["days_of_cover"])
    lines = [
        _item("In stock", f"{a['stock']} units, sells {fmt(a['avg_daily_sales'])} per day"),
        _item("Lasts", cover),
        _item("Expires in", "already expired" if a["expired"] else _days(a["expiry_days"])),
    ]
    if a["flagged"]:
        text = f"{a['name']} needs attention: {a['reason'][0].lower() + a['reason'][1:]}."
        lines.append(_item("What to do", a["action"], _level(a), a["sku"]))
    else:
        text = f"{a['name']} is fine. No action needed right now."
    return text, lines


def find_product(question, analyzed):
    q = question.lower()
    for a in analyzed:
        name = a["name"].lower()
        words = {name, name.split()[0], a["sku"].lower()}
        if any(len(w) >= 3 and re.search(r"\b" + re.escape(w) + r"s?\b", q) for w in words):
            return a
    return None


def detect_intent(question):
    q = question.lower()
    for intent, words in INTENTS:
        # \b at the start only, so "low" doesn't match "slow"
        if any(re.search(r"\b" + re.escape(w), q) for w in words):
            return intent
    return "help"


def _from_tips(tips):
    return [_item(s["name"], s["detail"], s["level"], s["sku"]) for s in tips]


def answer_question(question, result):
    analyzed = result["products"]
    insights = result["insights"]
    product = find_product(question, analyzed)
    intent = "product" if product else detect_intent(question)
    items = []

    if not analyzed:
        intent, text = "empty", "You don't have any products yet. Add them on the Inventory page and ask me again."

    elif intent == "product":
        text, items = _about_product(product)

    elif intent == "order":
        lst = result["purchase_list"]
        if lst:
            total = sum(i["qty"] for i in lst)
            text = f"Order {plural(len(lst), 'product')} ({total} units in total):"
            items = [_item(i["name"], f"{i['qty']} units", "urgent", i["sku"]) for i in lst]
        else:
            text = "Nothing needs ordering right now. All products are above their reorder level."

    elif intent == "expiring":
        soon = sorted((a for a in analyzed if a["near_expiry"]), key=lambda a: a["expiry_days"])
        if soon:
            text = f"{plural(len(soon), 'product')} expire within {result['threshold']} days:"
            items = [_item(a["name"],
                           ("Expired" if a["expired"] else f"{_days(a['expiry_days'])} left") + f" · {a['action']}",
                           _level(a), a["sku"]) for a in soon]
        else:
            text = f"Nothing expires within {result['threshold']} days."

    elif intent == "discount":
        offers = [s for s in insights["suggestions"] if s["kind"] in ("discount", "overstock")]
        text = "Put these on offer:" if offers else "No product needs a discount right now."
        items = _from_tips(offers)

    elif intent == "best":
        best = insights["best_sellers"]
        text = "Your best sellers:" if best else "No sales recorded yet."
        items = [_item(b["name"], f"{fmt(b['per_day'])} per day", "info", b["sku"]) for b in best]

    elif intent == "slow":
        slow = [s for s in insights["suggestions"] if s["kind"] in ("slow", "not_selling", "overstock")]
        text = "These are moving slowly:" if slow else "No slow-moving products right now."
        items = _from_tips(slow)

    elif intent == "tasks":
        todo = [a for a in result["priority"] if a["flagged"]][:5]
        text = "Here is what to do, most urgent first:" if todo else "Nothing needs your attention today."
        items = [_item(a["name"], a["action"], _level(a), a["sku"]) for a in todo]

    elif intent == "summary":
        h = insights["health"]
        text = (f"You have {h['total']} products: {h['low']} need ordering, {h['expiring']} are expiring soon, "
                f"{h['expired']} expired and {h['ok']} are fine.")
        urgent = [s for s in insights["suggestions"] if s["level"] == "urgent"]
        items = [_item(s["title"], s["detail"], "urgent", s["sku"]) for s in urgent[:3]]

    else:
        text = "I can answer questions about your stock. Try one of these:"

    return {"intent": intent, "answer": text, "items": items, "examples": EXAMPLE_QUESTIONS}


STATUS_WORDS = {"expired": "Expired", "low": "Need ordering", "expiring": "Expiring soon", "ok": "OK"}


def csv_safe(value):
    # excel runs cells starting with = + - @ as formulas
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def products_csv_rows(result):
    rows = [["Code", "Product", "In stock", "Sells per day", "Delivery (days)", "Expires in (days)",
             "Order at", "Lasts (days)", "Status", "Quantity to order", "What to do", "Why"]]
    for a in result["products"]:
        rows.append([csv_safe(v) for v in (
            a["sku"], a["name"], a["stock"], fmt(a["avg_daily_sales"]), a["lead_time_days"],
            a["expiry_days"], a["reorder_level"],
            "No sales" if a["days_of_cover"] is None else fmt(round(a["days_of_cover"], 1)),
            STATUS_WORDS[a["status"]], a["reorder_qty"], a["action"], a["reason"],
        )])
    return rows


def orders_csv_rows(result):
    rows = [["Code", "Product", "Quantity to order"]]
    rows += [[csv_safe(i["sku"]), csv_safe(i["name"]), i["qty"]] for i in result["purchase_list"]]
    rows.append(["", "Total units", sum(i["qty"] for i in result["purchase_list"])])
    return rows


USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,30}$")
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I


def validate_password(password, confirm):
    errors = {}
    if len(password) < 6:
        errors["password"] = "Use at least 6 characters"
    elif len(password) > 128:
        errors["password"] = "Use at most 128 characters"
    if password != confirm:
        errors["confirm"] = "Passwords don't match"
    return errors


def validate_account(username, password, confirm):
    errors = {}
    if not USERNAME_RE.match(username):
        errors["username"] = "Use 3-30 letters, numbers, dots, dashes or underscores"
    errors.update(validate_password(password, confirm))
    return errors


def new_recovery_code():
    chars = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(12))
    return "-".join(chars[i:i + 4] for i in range(0, 12, 4))


def normalize_code(code):
    return re.sub(r"[^A-Z0-9]", "", str(code).upper())


def _number(v):
    if isinstance(v, bool):  # True would pass float() as 1.0
        raise ValueError
    num = float(v)
    if math.isnan(num) or math.isinf(num):
        raise ValueError
    return num


def validate_product(data, existing_skus=()):
    if not isinstance(data, dict):
        return None, {"_": "Request body must be a JSON object"}
    errors, clean = {}, {}

    for f in REQUIRED_FIELDS:
        if data.get(f) is None or str(data.get(f)).strip() == "":
            errors[f] = "This field is required"

    sku = str(data.get("sku") or "").strip().upper()
    name = str(data.get("name") or "").strip()
    if "sku" not in errors:
        if len(sku) > 10:
            errors["sku"] = "SKU must be at most 10 characters"
        elif sku in {s.upper() for s in existing_skus}:
            errors["sku"] = f"SKU {sku} already exists"
    if "name" not in errors and len(name) > 100:
        errors["name"] = "Name must be at most 100 characters"
    clean["sku"], clean["name"] = sku, name

    for f in INT_FIELDS:
        if f in errors:
            continue
        try:
            num = _number(data[f])
            if not num.is_integer():
                raise ValueError
            num = int(num)
        except (TypeError, ValueError):
            errors[f] = "Must be a whole number"
            continue
        if num < 0:
            errors[f] = "Cannot be negative"
        elif num > MAX_INT:
            errors[f] = "Number is too large"
        else:
            clean[f] = num

    if "avg_daily_sales" not in errors:
        try:
            num = _number(data["avg_daily_sales"])
        except (TypeError, ValueError):
            errors["avg_daily_sales"] = "Must be a number"
        else:
            if num < 0:
                errors["avg_daily_sales"] = "Cannot be negative"
            elif num > 999999.99:  # fits DECIMAL(8,2)
                errors["avg_daily_sales"] = "Number is too large"
            else:
                clean["avg_daily_sales"] = round(num, 2)

    return (clean if not errors else None), errors
