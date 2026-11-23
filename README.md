# Smart Retail: Stock & Expiry Assistant

Sobhasaria Hackfest 2026, Track H01.

A web app for small shops to keep track of stock and expiry. It runs offline on the shop laptop (Flask + MySQL).

## Problem
Small shops track stock and expiry on paper. They run out of fast selling items and find expired stuff on the shelf too late. A plain low stock alert doesn't tell you what is about to expire or what to do first.

## Features
- **Overview**: counts of low stock / near expiry / expired items and a list of what to do today
- **Inventory**: search, filter, sort, add, edit and delete products
- **Expiry**: products grouped into expired, expiring soon and next 30 days
- **Reorder list**: how much to order for each product, with print and CSV export
- **Insights**: suggestions, top sellers, two simple charts and CSV reports
- **Assistant**: ask things like "what should I order?". It's keyword matching, not an AI model.
- **Settings**: change password, shop name, expiry warning days, load sample data, remove all products
- **Login**: first account is made on first run. More people can sign up at `/signup` with the signup code. Forgot password works with a recovery code.

## Files
```
app.py              flask routes
logic.py            all the rules and calculations
db.py               mysql queries
schema.sql          tables + 6 sample products
reset_password.py   reset password from the laptop
start.bat           double click to run on windows
templates/
static/
tests/
```

## Setup
Needs Python 3 and MySQL (I used XAMPP).

On Windows just double click `start.bat`. It makes the venv the first time, starts MySQL if it's not running and opens http://127.0.0.1:5000.

Manual way:
```
python -m venv venv
venv\Scripts\activate          (linux/mac: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env         (put your mysql password in .env)
python app.py
```
The database and sample products are created on the first run.

The signup code is printed when the app starts and saved in `.signup_code` (or set your own with `SIGNUP_CODE` in `.env`). Give it only to people who should get an account.

To open it from a phone on the same wifi, set `APP_HOST=0.0.0.0` in `.env` and open `http://<laptop-ip>:5000`.

Run tests (no MySQL needed):
```
python -m pytest -v
```

## Rules
| Rule | Formula |
|---|---|
| Low stock | `stock <= reorder_level` |
| Near expiry | `expiry_days <= threshold` (default 3) |
| Expired | `expiry_days <= 0` |
| Days of cover | `stock / avg_daily_sales` (infinite if sales is 0) |
| Reorder qty | `max(0, ceil(avg_daily_sales * (lead_time_days + 2) - stock))`, 2 = safety days |

Actions:
- low stock only: `Reorder N units`
- near expiry only: `Sell first / discount`
- both: `Reorder N units + sell old batch first`
- expired: `Remove from shelf`

Priority: low stock items with the least days of cover first, then expiry only items with the fewest days left, then the rest.

Suggestions on the Insights page:
| Suggestion | When |
|---|---|
| Remove from shelf | expired |
| Order today | low stock and `cover < lead_time` |
| Order soon | low stock but lasts until delivery |
| Put on offer | near expiry and some units won't sell in time |
| Too much in stock | stock takes longer to sell than its shelf life |
| Order earlier | `reorder_level < sales * lead_time`, suggests `ceil(sales * (lead_time + 1))` |
| Not selling | sales = 0 but stock left |
| Sells slowly | no flags and stock lasts more than 30 days |

Rule evaluation (Insights page): total flags, products flagged, baseline (what a low stock only check finds) and false alarms (every flag checked again against the raw numbers, should be 0).

## API
| Method | Route | What it does |
|---|---|---|
| GET | `/api/products?threshold=3` | all products with flags, priority, order list, metrics, insights |
| POST | `/api/products` | add product |
| PUT | `/api/products/<sku>` | edit product (sku can't change) |
| DELETE | `/api/products/<sku>` | delete product |
| DELETE | `/api/products` | remove all products |
| POST | `/api/reset` | load the 6 sample products again |
| POST | `/api/ask` | assistant, body `{"question": "..."}` |
| POST | `/api/account/password` | change password |
| GET | `/export/products.csv`, `/export/orders.csv` | CSV downloads |
| GET/POST | `/login`, `/setup`, `/signup`, `/forgot` | sign in, first run setup, sign up with code, reset password |
| POST | `/logout` | sign out |

Everything except login/setup/signup/forgot needs you to be signed in (API gives 401, pages redirect to login).

Security stuff: passwords and recovery codes are stored as salted hashes, login locks for 60s after 5 wrong tries, all SQL uses `%s` placeholders, and CSV cells starting with `= + - @` get a `'` in front so Excel doesn't run them.

## Assumptions
- `expiry_days` is for the oldest batch on the shelf, one batch per product
- `avg_daily_sales` is a fixed average
- supplier delivers exactly after `lead_time_days`
- no negative values, an expired item is entered as `expiry_days = 0`
- sku is unique and stored in upper case
- if a low stock item has reorder qty 0 it shows "Monitor stock" and isn't on the order list
- shop name and expiry window are saved in the browser, products in MySQL

## AI tools used
I used Claude Code for help with parts of the code and UI. I checked the rules against the problem statement and with the tests below.

## Limitations
- sample data is made up
- stock and sales are entered by hand, there is no billing
- one expiry date per product
- no CSV import
- all accounts can do everything (no staff roles), one store
- only MySQL
- no ML forecast since there's no sales history
- meant for the shop laptop / wifi, no HTTPS

## Future ideas
- billing screen that reduces stock
- batch wise expiry
- suppliers and purchase orders
- staff accounts

## Test results
All 40 tests pass (threshold = 3, sample data).

| # | Check | Expected | Result |
|---|---|---|---|
| 1 | Low stock | Bread, Milk, Yogurt | pass |
| 2 | Near expiry | Milk (3), Bread (1), Juice (2), Yogurt (2) | pass |
| 3 | No flags | Rice, Soap | pass |
| 4 | Juice | near expiry, not low, "Sell first / discount" | pass |
| 5 | Rice stock 25 → 6 → 25 | low stock, then cleared | pass |
| 6 | Reorder qty | Bread 26, Milk 28, Yogurt 16, Juice 0 | pass |
| 7 | Metrics | 7 flags on 4 products, baseline 3 of 7, 0 false alarms | pass |
| 8 | Priority | Bread, Milk, Yogurt, Juice, then Rice/Soap | pass |
| 9 | Sales = 0 | no crash, cover infinite, reorder 0 | pass |
| 10 | Expired (expiry 0) | "Remove from shelf" | pass |
| 11 | Huge stock (2 billion) | no flags, reorder 0 | pass |
| 12 | Threshold = 1 | only Bread near expiry | pass |
| 13 | Validation | duplicate sku, blanks, negatives, non numbers rejected | pass |
| 14 | Urgent orders | Bread, Milk, Yogurt "Order today" | pass |
| 15 | Waste / habits | Juice on offer (24 at risk), Milk and Bread order earlier, Soap slow | pass |
| 16 | Health | 3 need ordering, 1 expiring, 2 fine; best sellers Milk, Bread, Yogurt | pass |
| 17 | Edge cases | expired → remove, zero sales → not selling, overstock | pass |
