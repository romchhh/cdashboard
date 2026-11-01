"""
Тестовий скрипт для Google Search Console API
Використовує Service Account з settings/gsc_service_account.json
"""

import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ====== НАЛАШТУВАННЯ ======
SERVICE_ACCOUNT_FILE = "settings/gsc_service_account.json"
SITE_URL = "https://cosmy.ua"  # property URL як у Search Console (або sc-domain:example.com)
# ==========================

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def get_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("searchconsole", "v1", credentials=creds)


def test_list_sites(service):
    """Список всіх сайтів до яких є доступ"""
    print("\n📋 Список сайтів:")
    result = service.sites().list().execute()
    sites = result.get("siteEntry", [])
    if not sites:
        print("  ❌ Немає доступних сайтів. Перевір що service account доданий у Search Console.")
        return False
    for site in sites:
        print(f"  ✅ {site['siteUrl']} — {site['permissionLevel']}")
    return True


def test_search_analytics(service):
    """Тягнемо топ-5 запитів за останні 28 днів"""
    print(f"\n📊 Search Analytics для {SITE_URL}:")
    body = {
        "startDate": "2025-07-01",
        "endDate": "2025-08-01",
        "dimensions": ["query"],
        "rowLimit": 5,
    }
    try:
        result = service.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
        rows = result.get("rows", [])
        if not rows:
            print("  ⚠️  Даних немає (можливо сайт новий або URL невірний)")
            return
        print(f"  {'Запит':<40} {'Кліки':>7} {'Покази':>8} {'CTR':>7} {'Поз':>6}")
        print("  " + "-" * 72)
        for row in rows:
            query = row["keys"][0][:39]
            clicks = row.get("clicks", 0)
            impressions = row.get("impressions", 0)
            ctr = f"{row.get('ctr', 0)*100:.1f}%"
            pos = f"{row.get('position', 0):.1f}"
            print(f"  {query:<40} {clicks:>7} {impressions:>8} {ctr:>7} {pos:>6}")
    except Exception as e:
        print(f"  ❌ Помилка: {e}")


def main():
    print("🔑 Підключення до Google Search Console API...")
    try:
        service = get_service()
        print("  ✅ Авторизація успішна")
    except Exception as e:
        print(f"  ❌ Помилка авторизації: {e}")
        return

    ok = test_list_sites(service)
    if ok:
        test_search_analytics(service)

    print("\n✅ Тест завершено")


if __name__ == "__main__":
    main()