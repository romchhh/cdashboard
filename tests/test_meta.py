"""
Тестовий скрипт для Meta Ads API
"""

import requests

# ====== НАЛАШТУВАННЯ ======
ACCESS_TOKEN = "EAANJcmIJA2kBSXGuanUhilDZBYd5rIZBtmBJxFREpkb8L5OX93zl7Sy6BN8dxjdRG3kH8XrCGkQSIHT767UvgodwZCvwfOMWrHZBJfPf1z7nYYpG2lMV0GrBq24hU6ZBzNemRsgZB00aBGfwED2Gvq1SwDjKxXEHAFIRktLXEWGCZA8wI2sHlkadkaRY2sD67cZADFh32C9y8qzz5BVU"
AD_ACCOUNT_ID = "act_894307608248481"
API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"
# ==========================


def get(endpoint, params=None):
    p = {"access_token": ACCESS_TOKEN}
    if params:
        p.update(params)
    return requests.get(f"{BASE_URL}/{endpoint}", params=p).json()


def test_token():
    """Перевірка токена"""
    print("\n🔑 Перевірка токена:")
    data = get("me", {"fields": "id,name"})
    if "error" in data:
        print(f"  ❌ {data['error']['message']}")
        return False
    print(f"  ✅ Токен дійсний. App user: {data.get('name', data.get('id'))}")
    return True


def test_account_info():
    """Інфо про рекламний акаунт"""
    print(f"\n📋 Інфо про акаунт {AD_ACCOUNT_ID}:")
    data = get(AD_ACCOUNT_ID, {
        "fields": "name,account_status,currency,timezone_name,amount_spent"
    })
    if "error" in data:
        print(f"  ❌ {data['error']['message']}")
        return False

    statuses = {1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_REVIEW", 9: "IN_GRACE_PERIOD"}
    status = statuses.get(data.get("account_status"), str(data.get("account_status")))
    spent = int(data.get("amount_spent", 0)) / 100

    print(f"  ✅ Назва:    {data.get('name')}")
    print(f"     Статус:  {status}")
    print(f"     Валюта:  {data.get('currency')}")
    print(f"     Таймзон: {data.get('timezone_name')}")
    print(f"     Витрати: {spent:.2f} {data.get('currency')}")
    return True


def test_campaigns():
    """Список кампаній"""
    print("\n📊 Активні кампанії:")
    data = get(f"{AD_ACCOUNT_ID}/campaigns", {
        "fields": "name,status,objective,daily_budget,lifetime_budget",
        "effective_status": '["ACTIVE","PAUSED"]',
        "limit": 5
    })
    if "error" in data:
        print(f"  ❌ {data['error']['message']}")
        return

    campaigns = data.get("data", [])
    if not campaigns:
        print("  ⚠️  Кампаній не знайдено")
        return

    for c in campaigns:
        budget = ""
        if c.get("daily_budget"):
            budget = f"daily: {int(c['daily_budget'])/100:.0f}"
        elif c.get("lifetime_budget"):
            budget = f"lifetime: {int(c['lifetime_budget'])/100:.0f}"
        print(f"  • [{c['status']}] {c['name']} | {c.get('objective')} | {budget}")


def test_insights():
    """Статистика за останні 7 днів"""
    print("\n📈 Статистика за останні 7 днів:")
    data = get(f"{AD_ACCOUNT_ID}/insights", {
        "date_preset": "last_7d",
        "fields": "impressions,clicks,spend,ctr,cpc,reach",
        "level": "account"
    })
    if "error" in data:
        print(f"  ❌ {data['error']['message']}")
        return

    rows = data.get("data", [])
    if not rows:
        print("  ⚠️  Даних немає")
        return

    r = rows[0]
    print(f"  Покази:  {int(r.get('impressions', 0)):,}")
    print(f"  Кліки:   {int(r.get('clicks', 0)):,}")
    print(f"  Охоп:    {int(r.get('reach', 0)):,}")
    print(f"  Витрати: {float(r.get('spend', 0)):.2f}")
    print(f"  CTR:     {float(r.get('ctr', 0)):.2f}%")
    print(f"  CPC:     {float(r.get('cpc', 0)):.2f}")


def main():
    print("🚀 Тест Meta Ads API")
    ok = test_token()
    if not ok:
        print("\n❌ Токен невалідний — далі немає сенсу.")
        return
    test_account_info()
    test_campaigns()
    test_insights()
    print("\n✅ Тест завершено")


if __name__ == "__main__":
    main()