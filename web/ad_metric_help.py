"""
Пояснення показників рекламного дашборду (для підказок у UI).
"""

from __future__ import annotations

# Ключ → текст підказки (що це + як рахується + джерело)
METRIC_HELP: dict[str, str] = {
    "spend": (
        "Витрати на рекламу за обраний період.\n"
        "Як рахується: сума з Google Ads API та/або Meta Ads API; у зведенні конвертується в ₴ за курсом НБУ на дату періоду.\n"
        "Джерело: Google Ads / Meta Ads."
    ),
    "revenue": (
        "Дохід від успішних замовлень за обраний період.\n"
        "Як рахується: сума оплачених замовлень CRM Cosmy з атрибуцією до каналу (UTM / джерело).\n"
        "Джерело: CRM Cosmy."
    ),
    "revenue_crm": (
        "Дохід CRM — успішні замовлення з адмінки Cosmy, атрибутовані до цього каналу.\n"
        "Як рахується: сума order_total успішних замовлень за UTM/джерелом "
        "(для Meta: facebook.com, instagram, utm_medium=cpm тощо).\n"
        "Джерело: CRM Cosmy (фід адмінки сайту)."
    ),
    "revenue_google": (
        "Дохід Google — вартість конверсій з Google Ads (conversions_value).\n"
        "Як рахується: як колонка «Conv. value» у кабінеті — за датою кліку/взаємодії.\n"
        "Джерело: Google Ads API."
    ),
    "revenue_meta": (
        "Дохід Meta — вартість покупок з Ads Manager (purchase_value).\n"
        "Як рахується: сума action_values типу purchase / omni_purchase за період. Часто в USD і може бути 0, якщо піксель не передає цінність.\n"
        "Джерело: Meta Ads API."
    ),
    "roas": (
        "ROAS CRM (Return On Ad Spend) — окупність рекламних витрат.\n"
        "Як рахується: Дохід CRM (₴) ÷ Витрати (₴). Показується у відсотках (250% = 2,5× повернення).\n"
        "Джерело: CRM Cosmy + витрати в ₴ (курс НБУ для Meta/Google)."
    ),
    "roas_google": (
        "ROAS Google — окупність за даними Google Ads.\n"
        "Як рахується: conversions_value ÷ витрати Google (валюта акаунта). Формат: 2.50x.\n"
        "Відрізняється від ROAS CRM, який рахується за замовленнями Cosmy."
    ),
    "roas_meta": (
        "ROAS Meta — окупність за даними Meta Ads Manager.\n"
        "Як рахується: purchase_value ÷ витрати Meta (валюта акаунта). Формат: 2.50x.\n"
        "Відрізняється від ROAS CRM (дохід Cosmy ÷ витрати в ₴)."
    ),
    "cpa": (
        "CPA (Cost Per Acquisition) — вартість однієї конверсії/замовлення.\n"
        "Як рахується: Витрати ÷ Кількість конверсій (Google/Meta) або замовлень CRM.\n"
        "Джерело: Ads API + CRM."
    ),
    "cpa_crm": (
        "CPA CRM — вартість одного замовлення з адмінки Cosmy.\n"
        "Як рахується: Витрати каналу в ₴ ÷ CRM замовлення за UTM.\n"
        "Джерело: Ads API (витрати, конвертовані в ₴) + CRM Cosmy (замовлення)."
    ),
    "channel_share": (
        "Частка в «Інших» — частка доходу джерела всередині розділу «Інші джерела».\n"
        "Як рахується: Дохід джерела ÷ Сума доходів усіх джерел у цьому розділі.\n"
        "Не плутати з «Частка в доході» на KPI — це % від усього магазину."
    ),
    "meta_purchases": (
        "Покупки Meta — події purchase в Meta Ads Manager.\n"
        "Як рахується: purchase за датою конверсії (не показу) і атрибуцією акаунта.\n"
        "Може відрізнятись від CRM замовлень через вікно атрибуції та різні правила."
    ),
    "cpa_meta": (
        "CPA Meta — вартість однієї покупки за даними Meta Ads.\n"
        "Як рахується: Витрати Meta ÷ Покупки (purchase events).\n"
        "Джерело: Meta Ads API."
    ),
    "cpa_google": (
        "CPA Google — вартість однієї конверсії за даними Google Ads.\n"
        "Як рахується: Витрати ÷ Conversions (обидва за датою кліку, валюта акаунта).\n"
        "Відрізняється від CPA CRM, який рахується за замовленнями Cosmy в ₴."
    ),
    "orders": (
        "Кількість успішних замовлень за період.\n"
        "Як рахується: замовлення CRM зі статусом «успішне» та атрибуцією до каналу.\n"
        "Джерело: CRM Cosmy."
    ),
    "crm_orders": (
        "Замовлення CRM — успішні покупки, прив’язані до каналу за UTM.\n"
        "Як рахується: підрахунок замовлень CRM з utm_source / utm_medium / utm_campaign.\n"
        "Джерело: CRM Cosmy."
    ),
    "avg_check": (
        "Середній чек — середня сума одного замовлення.\n"
        "Як рахується: Дохід ÷ Кількість замовлень.\n"
        "Джерело: CRM Cosmy."
    ),
    "conversions": (
        "Конверсії — цільові дії в рекламному кабінеті (покупки, ліди тощо).\n"
        "Google Ads: metrics.conversions — як колонка «Conversions» / «Конверсії» "
        "(дата кліку/взаємодії в обраному періоді; не «by conv. time»).\n"
        "Meta: покупки за часом конверсії (action_report_time=conversion) і вікном атрибуції акаунта.\n"
        "Джерело: Google Ads / Meta Ads API."
    ),
    "conversion_rate": (
        "Коефіцієнт конверсії (CR) — частка візитів/кліків, що перетворились на замовлення CRM.\n"
        "Як рахується: (Замовлення CRM ÷ Трафік) × 100%. "
        "Для Google/Meta — трафік = кліки Ads; для SEO — кліки GSC (або сесії GA4); для Direct/Інші — сесії GA4.\n"
        "Джерело: CRM Cosmy + Ads API / GSC / GA4."
    ),
    "clicks": (
        "Кліки по оголошеннях за період.\n"
        "Як рахується: сума кліків з рекламного кабінету.\n"
        "Джерело: Google Ads / Meta Ads API."
    ),
    "ctr": (
        "CTR (Click-Through Rate) — частка кліків від показів.\n"
        "Як рахується: (Кліки ÷ Покази) × 100%.\n"
        "Джерело: Google Ads / Meta Ads / GSC."
    ),
    "organic_ctr": (
        "CTR з органічної видачі Google.\n"
        "Як рахується: (Кліки GSC ÷ Покази GSC) × 100%.\n"
        "Джерело: Google Search Console."
    ),
    "cpc": (
        "CPC (Cost Per Click) — середня вартість кліку.\n"
        "Як рахується: Витрати ÷ Кліки.\n"
        "Джерело: Google Ads / Meta Ads API."
    ),
    "cpm": (
        "CPM (Cost Per Mille) — вартість 1000 показів.\n"
        "Як рахується: (Витрати ÷ Покази) × 1000.\n"
        "Джерело: Meta Ads API."
    ),
    "impressions": (
        "Покази оголошень — скільки разів оголошення було показано.\n"
        "Як рахується: метрика impressions з рекламного кабінету.\n"
        "Джерело: Google Ads / Meta Ads API."
    ),
    "quality_score": (
        "Показник якості ключових слів у Google Ads (1–10).\n"
        "Як рахується: середнє quality score по активним ключам; вищий = краща релевантність і очікуваний CTR.\n"
        "Джерело: Google Ads API."
    ),
    "search_impression_share": (
        "Search Impression Share — частка показів у пошуковій видачі Google.\n"
        "Як рахується: (Ваші покази ÷ Можливі покази) × 100%.\n"
        "Джерело: Google Ads API."
    ),
    "reach": (
        "Охоплення — унікальні користувачі, які бачили оголошення.\n"
        "Як рахується: метрика reach з Meta Ads.\n"
        "Джерело: Meta Ads API."
    ),
    "frequency": (
        "Частота показів на одного користувача.\n"
        "Як рахується: Покази ÷ Охоплення.\n"
        "Джерело: Meta Ads API."
    ),
    "gsc_clicks": (
        "Кліки з органічної видачі Google (Search Console).\n"
        "Як рахується: сума кліків по всіх запитах/сторінках за період.\n"
        "Джерело: Google Search Console."
    ),
    "organic_traffic": (
        "Органічний трафік — кліки з Google Search (не реклама).\n"
        "Як рахується: кліки GSC за період.\n"
        "Джерело: Google Search Console."
    ),
    "avg_position": (
        "Середня позиція сайту в органічній видачі Google.\n"
        "Як рахується: середнє значення position по запитах GSC (менше = вище в видачі).\n"
        "Джерело: Google Search Console."
    ),
    "ga4_sessions": (
        "Сесії GA4 — візити користувачів на сайт.\n"
        "Як рахується: sessions з Google Analytics 4 для відповідного каналу.\n"
        "Джерело: Google Analytics 4."
    ),
    "sessions": (
        "Сесії — візити на сайт за обраний період.\n"
        "Як рахується: sessions з GA4 (або замовлення CRM, якщо GA4 недоступний).\n"
        "Джерело: GA4 / CRM."
    ),
    "kw_top10": (
        "Кількість ключових слів у ТОП-10 органічної видачі.\n"
        "Як рахується: запити GSC з середньою позицією ≤ 10.\n"
        "Джерело: Google Search Console."
    ),
    "kw_top3": (
        "Кількість ключових слів у ТОП-3 органічної видачі.\n"
        "Як рахується: запити GSC з середньою позицією ≤ 3.\n"
        "Джерело: Google Search Console."
    ),
    "indexed_pages": (
        "Індексовані сторінки — сторінки сайту в індексі Google.\n"
        "Як рахується: кількість проіндексованих URL з GSC.\n"
        "Джерело: Google Search Console."
    ),
    "referring_domains": (
        "Реферальні домени — унікальні сайти, що посилаються на вас.\n"
        "Як рахується: кількість referring domains з GA4/GSC.\n"
        "Джерело: Google Analytics 4."
    ),
    "bounce_rate": (
        "Показник відмов — частка сесій без взаємодії з сайтом.\n"
        "Як рахується: (Сесії з 1 переглядом ÷ Усі сесії) × 100%. Нижче — зазвичай краще.\n"
        "Джерело: Google Analytics 4."
    ),
    "pages_per_session": (
        "Сторінок за сесію — середня глибина перегляду.\n"
        "Як рахується: Загальні перегляди сторінок ÷ Сесії.\n"
        "Джерело: Google Analytics 4."
    ),
    "revenue_share": (
        "Частка каналу в загальному доході магазину.\n"
        "Як рахується: (Дохід каналу ÷ Загальний дохід CRM) × 100%.\n"
        "Джерело: CRM Cosmy."
    ),
    "n_sources": (
        "Кількість активних джерел трафіку за період.\n"
        "Як рахується: унікальні джерела/домени з GA4 або CRM.\n"
        "Джерело: GA4 / CRM."
    ),
    "transactions": (
        "Кількість транзакцій (замовлень) у деталізації.\n"
        "Як рахується: успішні замовлення CRM за період.\n"
        "Джерело: CRM Cosmy."
    ),
    "delta_orders": (
        "Зміна кількості замовлень vs попередній період.\n"
        "Як рахується: ((Поточні − Попередні) ÷ Попередні) × 100%. Якщо попередніх не було — показуємо 100% при появі нових.\n"
        "Порівняння: обраний режим (попередній період або рік тому)."
    ),
    "delta_revenue": (
        "Зміна доходу vs попередній період.\n"
        "Як рахується: ((Поточний дохід − Попередній) ÷ Попередній) × 100%.\n"
        "Порівняння: обраний режим (попередній період або рік тому)."
    ),
    "channel": (
        "Канал маркетингу — джерело трафіку/продажів.\n"
        "Як визначається: Google Ads, Meta Ads, SEO, Direct, Інші — за UTM і правилами атрибуції CRM/GA4."
    ),
    "share": (
        "Частка показника від загальної суми в таблиці/каналі.\n"
        "Як рахується: (Значення рядка ÷ Сума всіх рядків) × 100%."
    ),
    "brand": (
        "Бренд товару з замовлення CRM.\n"
        "Як рахується: групування рядків замовлень за брендом; дохід і кількість — сума по бренду.\n"
        "Джерело: CRM Cosmy."
    ),
    "spend_estimated": (
        "Оціночні витрати по бренду.\n"
        "Як рахується: частка витрат каналу пропорційно доходу бренду (не UTM-атрибуція).\n"
        "Джерело: розрахунок на основі Ads API + CRM."
    ),
    "campaign": (
        "Рекламна кампанія в Google Ads або Meta Ads.\n"
        "Джерело: Ads API. Дохід/замовлення CRM — за utm_campaign, якщо є збіг."
    ),
    "campaign_type": (
        "Тип кампанії Google Ads (Search, Performance Max, Display тощо).\n"
        "Джерело: Google Ads API."
    ),
    "campaign_objective": (
        "Ціль кампанії Meta (продажі, трафік, ліди тощо).\n"
        "Джерело: Meta Ads API."
    ),
    "keyword": (
        "Ключове слово в Google Ads або органічний запит у GSC.\n"
        "Джерело: Google Ads API / Search Console."
    ),
    "match_type": (
        "Тип відповідності ключового слова (точний, фразовий, широкий).\n"
        "Джерело: Google Ads API."
    ),
    "position": (
        "Середня позиція в органічній видачі Google для запиту.\n"
        "Як рахується: середнє position з GSC (1 = перше місце).\n"
        "Джерело: Google Search Console."
    ),
    "position_change": (
        "Зміна позиції vs попередній період.\n"
        "Як рахується: Позиція попереднього періоду − Поточна (плюс = покращення, сайт піднявся вище)."
    ),
    "url": (
        "URL сторінки в Search Console або посадкова сторінка в GA4."
    ),
    "landing_page": (
        "Посадкова сторінка — перша сторінка візиту користувача.\n"
        "Джерело: Google Analytics 4."
    ),
    "source": (
        "Джерело трафіку (домен, UTM source, email-розсилка тощо).\n"
        "Як визначається: referrer / UTM з CRM або канали GA4."
    ),
    "source_type": (
        "Тип джерела: email, social, referral, affiliate, інше.\n"
        "Як визначається: автоматична класифікація за назвою джерела."
    ),
    "category": (
        "Категорія товарів у замовленнях CRM.\n"
        "Як рахується: сума доходу по категорії за період.\n"
        "Джерело: CRM Cosmy."
    ),
    "marketing_channel": (
        "Маркетинговий канал за UTM-атрибуцією.\n"
        "Як визначається: utm_source / utm_medium / utm_campaign замовлення "
        "(або перше джерело клієнта Cosmy) → Google Ads, Meta, SEO, Direct, Email, Referral, Інші.\n"
        "Збігається з таблицею «Канали» на огляді. Не плутати з source_category з фіду Cosmy."
    ),
    "utm_source": (
        "utm_source — джерело в UTM-мітці (google, facebook.com, newsletter тощо).\n"
        "Пріоритет: поле замовлення → source_utm_source клієнта."
    ),
    "utm_medium": (
        "utm_medium — тип трафіку (cpc, cpm, organic, email, referral тощо).\n"
        "Разом з utm_source визначає маркетинговий канал."
    ),
    "utm_campaign": (
        "utm_campaign — назва кампанії в UTM.\n"
        "Використовується для прив’язки CRM-замовлень до рядків Google/Meta кампаній."
    ),
    "cosmy_source_category": (
        "source_category з фіду Cosmy (ads / organic / unknown) — груба класифікація на рівні клієнта.\n"
        "Не збігається 1:1 з UTM-каналами. Довідковий блок, не для ROAS/CPA."
    ),
    "seo_spend": (
        "Витрати на SEO — ручні записи на сторінці SEO (дата, сума, опис).\n"
        "Як рахується: сума всіх витрат, дата яких потрапляє в обраний період звіту.\n"
        "Використовується для ROAS CRM у рядку SEO на огляді."
    ),
    "city": (
        "Місто доставки замовлення.\n"
        "Джерело: CRM Cosmy."
    ),
    "device": (
        "Пристрій користувача (desktop, mobile, tablet).\n"
        "Джерело: Google Analytics 4."
    ),
    "audience_segment": (
        "Сегмент аудиторії Meta (стать / вік).\n"
        "Джерело: Meta Ads API."
    ),
    "customer_new": (
        "Нові покупці — клієнти з першим замовленням у періоді.\n"
        "Джерело: CRM Cosmy."
    ),
    "customer_returning": (
        "Повторні покупці — клієнти з більш ніж одним замовленням.\n"
        "Джерело: CRM Cosmy."
    ),
    "order_id": (
        "Номер замовлення в CRM Cosmy."
    ),
    "order_date": (
        "Дата успішного замовлення в CRM."
    ),
    "gsc_impressions": (
        "Покази в органічній видачі Google.\n"
        "Джерело: Google Search Console."
    ),
}

# Підпис UI → ключ глосарію (для автопідказок у KPI і заголовках таблиць)
LABEL_TO_METRIC_KEY: dict[str, str] = {
    "Витрати": "spend",
    "Дохід": "revenue",
    "Дохід CRM": "revenue_crm",
    "Дохід Google": "revenue_google",
    "Дохід Meta": "revenue_meta",
    "ROAS": "roas",
    "ROAS CRM": "roas",
    "ROAS Meta": "roas_meta",
    "Конверсії Google": "conversions",
    "Частка в «Інших»": "channel_share",
    "CPA": "cpa",
    "CPA CRM": "cpa_crm",
    "CPA Meta": "cpa_meta",
    "CPA (Meta)": "cpa_meta",
    "Покупки Meta": "meta_purchases",
    "Замовлення": "orders",
    "CRM замовлення": "crm_orders",
    "Середній чек": "avg_check",
    "Конверсії": "conversions",
    "Конверсія": "conversion_rate",
    "Коефіцієнт конверсії": "conversion_rate",
    "Кліки": "clicks",
    "CTR": "ctr",
    "CTR (з видачі)": "organic_ctr",
    "CPC": "cpc",
    "CPM": "cpm",
    "Покази": "impressions",
    "Показник якості": "quality_score",
    "Search Impr. Share": "search_impression_share",
    "Охоплення": "reach",
    "Частота": "frequency",
    "Покупки Meta": "meta_purchases",
    "Кліки GSC": "gsc_clicks",
    "Органічний трафік": "organic_traffic",
    "Середня позиція": "avg_position",
    "Сесії GA4": "ga4_sessions",
    "Сесії": "sessions",
    "KW в ТОП-10": "kw_top10",
    "KW в ТОП-3": "kw_top3",
    "Індексовані сторінки": "indexed_pages",
    "Реферальні домени": "referring_domains",
    "Показник відмов": "bounce_rate",
    "Сторінок / сесію": "pages_per_session",
    "Сторінок/сесію": "pages_per_session",
    "Частка в доході": "revenue_share",
    "К-сть джерел": "n_sources",
    "Транзакції": "transactions",
    "Канал": "channel",
    "Частка": "share",
    "Частка доходу": "share",
    "Частка від доходу": "share",
    "Бренд": "brand",
    "Витрати ≈": "spend_estimated",
    "Δ замовлення": "delta_orders",
    "Δ дохід": "delta_revenue",
    "Δ покупки": "delta_orders",
    "Кампанія": "campaign",
    "Тип": "campaign_type",
    "Тип відповідності": "match_type",
    "Ціль": "campaign_objective",
    "Ключове слово": "keyword",
    "Позиція": "position",
    "Зміна": "position_change",
    "URL": "url",
    "Сторінка": "landing_page",
    "Кліки GSC": "gsc_clicks",
    "Джерело (домен)": "source",
    "Категорія": "category",
    "Канал": "marketing_channel",
    "utm_source": "utm_source",
    "utm_medium": "utm_medium",
    "utm_campaign": "utm_campaign",
    "source_category": "cosmy_source_category",
    "Витрати SEO": "seo_spend",
    "Місто": "city",
    "Пристрій": "device",
    "Сегмент": "audience_segment",
    "Дата": "order_date",
    "№ замовлення": "order_id",
    "Джерело": "source",
    "Кампанія": "campaign",
    "Якість": "quality_score",
    "Середня позиція GSC": "avg_position",
    "Покупки": "meta_purchases",
    "Нові vs повторні покупці": "customer_new",
    "Нові покупці": "customer_new",
    "Повторні покупці": "customer_returning",
    "Сторінка": "landing_page",
    "№": "order_id",
    "Тип": "campaign_type",
}


def metric_help(key: str | None) -> str | None:
    """Текст підказки за ключем або підписом."""
    if not key:
        return None
    k = key.strip()
    if k in METRIC_HELP:
        return METRIC_HELP[k]
    mapped = LABEL_TO_METRIC_KEY.get(k)
    if mapped:
        return METRIC_HELP.get(mapped)
    return None


def metric_key_for_label(label: str | None) -> str | None:
    if not label:
        return None
    return LABEL_TO_METRIC_KEY.get(label.strip())
