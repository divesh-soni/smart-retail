import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logic  # noqa: E402

THRESHOLD = 3

# same 6 products as schema.sql
SEED = [
    {"sku": "R101", "name": "Milk 1L",  "stock": 8,  "avg_daily_sales": 12, "lead_time_days": 1, "expiry_days": 3,   "reorder_level": 10},
    {"sku": "R102", "name": "Rice 5kg", "stock": 25, "avg_daily_sales": 2,  "lead_time_days": 3, "expiry_days": 180, "reorder_level": 8},
    {"sku": "R103", "name": "Bread",    "stock": 4,  "avg_daily_sales": 10, "lead_time_days": 1, "expiry_days": 1,   "reorder_level": 6},
    {"sku": "R104", "name": "Juice",    "stock": 30, "avg_daily_sales": 3,  "lead_time_days": 2, "expiry_days": 2,   "reorder_level": 10},
    {"sku": "R105", "name": "Yogurt",   "stock": 5,  "avg_daily_sales": 7,  "lead_time_days": 1, "expiry_days": 2,   "reorder_level": 8},
    {"sku": "R106", "name": "Soap",     "stock": 40, "avg_daily_sales": 1,  "lead_time_days": 4, "expiry_days": 300, "reorder_level": 10},
]


def run(products=SEED):
    result = logic.analyze_inventory([dict(p) for p in products], THRESHOLD)
    return result, {a["sku"]: a for a in result["products"]}


def test_1_low_stock():
    _, by = run()
    low = {sku for sku, a in by.items() if a["low_stock"]}
    assert low == {"R103", "R101", "R105"}


def test_2_near_expiry():
    _, by = run()
    near = {sku for sku, a in by.items() if a["near_expiry"]}
    assert near == {"R101", "R103", "R104", "R105"}
    assert by["R101"]["expiry_days"] == 3
    assert by["R103"]["expiry_days"] == 1
    assert by["R104"]["expiry_days"] == 2
    assert by["R105"]["expiry_days"] == 2


def test_3_rice_and_soap_have_no_flags():
    _, by = run()
    for sku in ("R102", "R106"):
        assert by[sku]["flags"] == []
        assert by[sku]["flagged"] is False
        assert by[sku]["action"] == ""


def test_4_juice_sell_first_not_reorder():
    _, by = run()
    juice = by["R104"]
    assert juice["near_expiry"] is True
    assert juice["low_stock"] is False
    assert juice["action"] == "Sell first / discount"
    assert "reorder" not in juice["action"].lower()


def test_5_rice_stock_change_toggles_low_stock():
    products = [dict(p) for p in SEED]
    rice = next(p for p in products if p["sku"] == "R102")
    rice["stock"] = 6
    _, by = run(products)
    assert by["R102"]["low_stock"] is True
    rice["stock"] = 25
    _, by = run(products)
    assert by["R102"]["low_stock"] is False
    assert by["R102"]["flags"] == []


def test_6_reorder_quantities():
    _, by = run()
    assert by["R103"]["reorder_qty"] == 26  # 10*(1+2) - 4
    assert by["R101"]["reorder_qty"] == 28  # 12*(1+2) - 8
    assert by["R105"]["reorder_qty"] == 16  # 7*(1+2) - 5
    assert by["R104"]["reorder_qty"] == 0  # negative, so 0
    assert logic.reorder_qty(30, 3, 2) == 0
    for a in by.values():
        assert a["reorder_qty"] >= 0


def test_metrics_7_flags_on_4_products():
    result, _ = run()
    m = result["metrics"]
    assert m["total_flags"] == 7
    assert m["products_flagged"] == 4
    assert m["baseline_flags"] == 3
    assert m["false_alarms"] == 0


def test_priority_order():
    result, _ = run()
    order = [a["sku"] for a in result["priority"]]
    # bread 0.4, milk 0.67, yogurt 0.71 days cover, then juice
    assert order[:4] == ["R103", "R101", "R105", "R104"]
    assert set(order[4:]) == {"R102", "R106"}


def test_actions_and_reasons():
    _, by = run()
    assert by["R103"]["action"] == "Reorder 26 units + sell old batch first"
    assert by["R103"]["reason"].startswith("4 left, sells 10/day, lasts 0.4 days")
    for a in by.values():
        if a["flagged"]:
            assert a["reason"] != ""


def test_purchase_list_only_reorder_items():
    result, _ = run()
    assert [(i["sku"], i["qty"]) for i in result["purchase_list"]] == [
        ("R103", 26), ("R101", 28), ("R105", 16)]


def test_edge_zero_sales_no_divide_by_zero():
    p = {"sku": "Z1", "name": "Slow item", "stock": 2, "avg_daily_sales": 0,
         "lead_time_days": 2, "expiry_days": 100, "reorder_level": 5}
    a = logic.analyze_product(p, THRESHOLD)
    assert logic.days_of_cover(2, 0) == math.inf
    assert a["days_of_cover"] is None
    assert a["low_stock"] is True
    assert a["reorder_qty"] == 0
    assert "unlimited" in a["reason"]


def test_edge_expired_product():
    p = {"sku": "X1", "name": "Old cake", "stock": 3, "avg_daily_sales": 1,
         "lead_time_days": 1, "expiry_days": 0, "reorder_level": 1}
    a = logic.analyze_product(p, THRESHOLD)
    assert a["expired"] is True
    assert a["near_expiry"] is True
    assert a["action"] == "Remove from shelf"
    assert "EXPIRED" in a["flags"]


def test_edge_huge_stock():
    p = {"sku": "H1", "name": "Bulk salt", "stock": 2_000_000_000, "avg_daily_sales": 5,
         "lead_time_days": 3, "expiry_days": 365, "reorder_level": 10}
    a = logic.analyze_product(p, THRESHOLD)
    assert a["flagged"] is False
    assert a["reorder_qty"] == 0
    assert logic.reorder_qty(2_000_000_000, 5, 3) == 0


def test_edge_threshold_is_configurable():
    result = logic.analyze_inventory([dict(p) for p in SEED], threshold=1)
    near = {a["sku"] for a in result["products"] if a["near_expiry"]}
    assert near == {"R103"}


def test_validation_rejects_bad_input():
    _, errors = logic.validate_product({"sku": "R101", "name": "", "stock": -1,
                                        "avg_daily_sales": "abc", "lead_time_days": 1.5,
                                        "expiry_days": 2, "reorder_level": 3},
                                       existing_skus=["R101"])
    assert errors["sku"] == "SKU R101 already exists"
    assert errors["name"] == "This field is required"
    assert errors["stock"] == "Cannot be negative"
    assert errors["avg_daily_sales"] == "Must be a number"
    assert errors["lead_time_days"] == "Must be a whole number"


def test_validation_accepts_good_input():
    clean, errors = logic.validate_product({"sku": "n201", "name": "Eggs", "stock": "12",
                                            "avg_daily_sales": "4.5", "lead_time_days": 1,
                                            "expiry_days": 7, "reorder_level": 6})
    assert errors == {}
    assert clean["sku"] == "N201" and clean["stock"] == 12 and clean["avg_daily_sales"] == 4.5


def _tips(result):
    return {(t["sku"], t["kind"]): t for t in result["insights"]["suggestions"]}


def test_insights_urgent_orders_first():
    result, _ = run()
    tips = result["insights"]["suggestions"]
    urgent = {t["sku"] for t in tips if t["level"] == "urgent"}
    assert urgent == {"R101", "R103", "R105"}
    assert tips[0]["level"] == "urgent"


def test_insights_waste_and_habits():
    result, _ = run()
    tips = _tips(result)
    assert ("R104", "discount") in tips
    assert "24" in tips[("R104", "discount")]["detail"]
    assert ("R101", "reorder_level") in tips
    assert "24" in tips[("R101", "reorder_level")]["detail"]
    assert ("R103", "reorder_level") in tips
    assert ("R106", "slow") in tips
    assert not any(sku == "R102" for sku, _ in tips)


def test_insights_health_counts():
    result, _ = run()
    h = result["insights"]["health"]
    assert h == {"expired": 0, "low": 3, "expiring": 1, "ok": 2, "total": 6}
    assert [b["sku"] for b in result["insights"]["best_sellers"]] == ["R101", "R103", "R105"]


def test_insights_expired_and_not_selling():
    products = [
        {"sku": "X1", "name": "Old cake", "stock": 3, "avg_daily_sales": 1,
         "lead_time_days": 1, "expiry_days": 0, "reorder_level": 1},
        {"sku": "Z1", "name": "Slow item", "stock": 2, "avg_daily_sales": 0,
         "lead_time_days": 2, "expiry_days": 100, "reorder_level": 1},
    ]
    tips = _tips(logic.analyze_inventory(products, THRESHOLD))
    assert tips[("X1", "remove")]["level"] == "urgent"
    assert ("Z1", "not_selling") in tips


def test_insights_overstock_before_expiry():
    p = {"sku": "O1", "name": "Cheese", "stock": 100, "avg_daily_sales": 2,
         "lead_time_days": 2, "expiry_days": 20, "reorder_level": 5}
    tips = _tips(logic.analyze_inventory([p], THRESHOLD))
    assert ("O1", "overstock") in tips


def ask(q, products=SEED):
    return logic.answer_question(q, logic.analyze_inventory([dict(p) for p in products], THRESHOLD))


def test_assistant_order():
    a = ask("What should I order?")
    assert a["intent"] == "order"
    assert [(i["title"], i["detail"]) for i in a["items"]] == [
        ("Bread", "26 units"), ("Milk 1L", "28 units"), ("Yogurt", "16 units")]


def test_assistant_expiring():
    a = ask("what expires soon")
    assert a["intent"] == "expiring"
    assert [i["title"] for i in a["items"]] == ["Bread", "Juice", "Yogurt", "Milk 1L"]


def test_assistant_product_lookup():
    a = ask("How is milk doing?")
    assert a["intent"] == "product"
    assert a["answer"].startswith("Milk 1L needs attention")
    assert ask("tell me about rice")["answer"] == "Rice 5kg is fine. No action needed right now."
    assert ask("juices?")["intent"] == "product"


def test_assistant_other_intents():
    assert ask("What should I do first?")["items"][0]["title"] == "Bread"
    assert ask("which items are slow")["intent"] == "slow"  # not "low"
    assert ask("what to discount")["items"][0]["title"] == "Juice"
    assert ask("best sellers")["items"][0]["title"] == "Milk 1L"
    assert "6 products" in ask("give me a summary")["answer"]
    assert ask("hello there")["intent"] == "help"
    assert ask("what should I order?", [])["intent"] == "empty"


def test_assistant_brief_questions():
    # "most urgent" should be tasks, not best sellers
    a = ask("Which items are most urgent today?")
    assert a["intent"] == "tasks" and a["items"][0]["sku"] == "R103"
    assert ask("Which products need reordering?")["intent"] == "order"
    assert ask("Which products are nearing expiry?")["intent"] == "expiring"
    why = ask("Why is Bread marked as low stock?")
    assert why["intent"] == "product" and "4 left" in why["answer"]
    assert ask("What should I order?")["items"][0]["sku"] == "R103"
