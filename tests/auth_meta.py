"""
Тестовий скрипт для Meta Ads API
"""

import requests

# ====== НАЛАШТУВАННЯ ======
ACCESS_TOKEN = "EAANJcmIJA2kBScDO2wglc8wv9BjXl9Bfs0m3Ccx8yrRmOex96CfyrWyp3YZBfXfe68GbNBgoIhWY8b7gLtKZBU8FSD7xZAt8yRfkpT0ZBZBTPIkB681epdgPWqQkGybJTZBAVAx0pZCetszazZBLelhm3Jl45DPHFQmFWMlQ9ILAd3664FjVKOvp2ZAELnsMRNZCvFQlTnPNnVVa0Yty7cpAfZBZBBLOziOevy6ZBq6rmprga0D4ATA1xuwiQajMCuAUL7NQWZAAgygoesZBf8EMkfD4lgWZA3KFAPogfBsets0ZD"  # короткочасний User Access Token (з Graph API Explorer)
APP_ID = "925180549989225"        # ЧИСЛО з Meta App Dashboard -> Settings -> Basic (НЕ act_...)
APP_SECRET = "09baeb7b90cf9a8c4bb9955ba1b433a0"    # з Meta App Dashboard -> Settings -> Basic
AD_ACCOUNT_ID = "act_894307608248481"
API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"
# ==========================


# Активний токен, яким реально користуються всі запити.
# Спочатку дорівнює короткочасному ACCESS_TOKEN, але main() підмінює
# його на довготривалий одразу після обміну.
current_token = ACCESS_TOKEN


def get(endpoint, params=None):
    p = {"access_token": current_token}
    if params:
        p.update(params)
    return requests.get(f"{BASE_URL}/{endpoint}", params=p).json()


def exchange_for_long_lived_token():
    """
    Обмінює короткочасний токен (1-2 год) на довготривалий (~60 днів).

    Важливо:
    - Короткочасний токен потрібно взяти заново з Graph API Explorer
      (https://developers.facebook.com/tools/explorer/), бо старий
      міг вже протухнути.
    - APP_ID — це ЧИСЛО з Meta App Dashboard -> Settings -> Basic
      (не плутати з act_... — це ID рекламного акаунта, інша річ).
    - Довготривалий User Token не можна продовжити ще раз — за 60 днів
      треба буде повторити авторизацію користувача заново.
    """
    print("\n🔄 Обмін токена на довготривалий (60 днів):")

    if APP_ID == "ВАШ_APP_ID" or APP_SECRET == "ВАШ_APP_SECRET":
        print("  ❌ Спочатку впиши APP_ID та APP_SECRET у налаштуваннях скрипта")
        return None

    params = {
        "grant_type": "fb_exchange_token",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "fb_exchange_token": ACCESS_TOKEN,
    }

    resp = requests.get(f"{BASE_URL}/oauth/access_token", params=params).json()

    if "error" in resp:
        print(f"  ❌ {resp['error']['message']}")
        return None

    long_token = resp.get("access_token")
    expires_in = resp.get("expires_in")  # у секундах, зазвичай ~5184000 (60 днів)

    days = round(expires_in / 86400, 1) if expires_in else "?"

    print(f"  ✅ Новий токен отримано, дійсний ~{days} днів")
    print(f"  🔑 Токен: {long_token}")

    # Зберігаємо у файл, щоб не загубити
    with open("long_lived_token.txt", "w") as f:
        f.write(long_token)
    print("  💾 Збережено у long_lived_token.txt")

    return long_token


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
    global current_token

    print("🚀 Тест Meta Ads API")

    # Спочатку обмінюємо короткочасний токен на довготривалий (60 днів)
    # і всі подальші запити йдуть уже через нього.
    long_token = exchange_for_long_lived_token()
    if long_token:
        current_token = long_token

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