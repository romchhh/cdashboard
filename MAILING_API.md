# Mailing API — тригерні листи Cosmy

API для відправки транзакційних листів з сайту OpenCart. **Усі дані про товари та замовлення передаються з сайту** в тілі запиту (`items[]`). Бекенд лише збирає HTML і відправляє через Resend.

## База URL

```text
https://YOUR-DOMAIN
```

Локально: `http://127.0.0.1:8000`

## Авторизація

Заголовок `X-API-Key` або query `api_key=...` (значення `MARKETING_API_KEY` у `.env`).

## Загальні поля

| Поле | Опис |
|------|------|
| `email` | **Обов'язково.** Отримувач |
| `name` / `first_name` | Ім'я в привітанні |
| `subject` | Опційно — перевизначити тему листа |
| `items[]` | **Обов'язково** для листів з товарами (див. структуру нижче) |

Для **замовлення** можна передати поля в корені або в об'єкті `order` (зручно для OpenCart).

**Обов’язково для блоку підсумків у листі «Замовлення прийнято»** (як у кошику):

| Поле | Опис |
|------|------|
| `discount` | Знижка Cosmy club / промокод, **грн** (число) |
| `order_total` | «Разом» — загальна сума замовлення, **грн** (те саме, що «загалом» у кошику) |

```json
{
  "order": {
    "order_number": "10482",
    "order_total": 2230,
    "discount": 250,
    "payment_method": "Оплата карткою онлайн",
    "delivery_address": "м. Київ, НП №1",
    "delivery_info": "Безкоштовна доставка"
  },
  "items": [ ... ]
}
```

### Структура `items[]` (товар)

```json
{
  "product_id": 12345,
  "name": "Сироватка з вітаміном C",
  "image_url": "https://cosmy.com.ua/image/catalog/....jpg",
  "product_url": "https://cosmy.com.ua/....?utm_source=email&utm_campaign=cart",
  "price": 1290,
  "price_old": 1490,
  "quantity": 2,
  "old_price": 1490,
  "new_price": 1290
}
```

| Поле | Коли потрібно |
|------|----------------|
| `name` | **Завжди** |
| `image_url` | Рекомендовано (абсолютне HTTPS-посилання) |
| `product_url` | Рекомендовано — **з UTM-мітками** для аналітики |
| `price` | Ціна в листі (актуальна / зі знижкою) |
| `price_old` | Опційно — стара ціна (закреслення, «зі знижкою») |
| `quantity` | Для замовлення / кошика (за замовч. 1) |
| `old_price`, `new_price` | **Обов'язково** для листів про зниження ціни |

`product_id` — опційно; якщо немає `image_url`, можна підставити з каталогу бота.

У листі для кожного товару: **клікабельні фото, назва та кнопка «Переглянути»** → `product_url`.

---

## 1) Welcome — реєстрація

`POST /api/mailings/welcome`

```json
{
  "email": "client@example.com",
  "name": "Дарія"
}
```

---

## 2) Замовлення прийнято

`POST /api/mailings/order`

Тригер після оформлення замовлення (лист «Замовлення отримано / прийнято»). Сайт передає товари та підсумки **як у кошику**.

**Тема (за замовч.):** `Ваше замовлення успішно прийнято та передано в роботу ✨`

### Поля підсумків (як у кошику)

У блок листа підтягуються **2 поля** з кошика / замовлення:

| Поле API | Що передає сайт (кошик) | У листі |
|----------|-------------------------|---------|
| `discount` | Розмір знижки за **Cosmy club** (лояльність) **або промокодом**, число в **грн** | `Знижка Cosmy club / Промокод: −250 грн` |
| `order_total` | Підсумкова сума замовлення («**Разом**» / те, що зараз «загалом» у кошику), грн | `Разом: 2 230 грн` |

Обидва поля **опційні**, але рекомендовані: без них у листі не буде рядків знижки / «Разом».

Аліаси (корінь або `order{}`):

- сума: `order_total`, `total`, `grand_total`, `total_amount`, `amount`, `sum`
- знижка: `discount`, `discount_amount`, `loyalty_discount`, `promo_discount`, `coupon_discount`, `club_discount`, `cosmy_club_discount`, `reward_discount`, `voucher_discount`

Від’ємне значення знижки (як іноді в OpenCart, напр. `-250`) приймається — у листі показується модуль.

Альтернатива: масив `totals[]` / `order_totals[]` у стилі OpenCart (`code` + `value`). Знижка береться з кодів `coupon`, `voucher`, `reward`, `discount`, `loyalty`, `club`, `cosmy_club`, `promo`…; «Разом» — з `total` / `grand_total` / `order_total`.

```json
{
  "email": "cosmy.ua@gmail.com",
  "name": "Марія",
  "lastname": "Потапенко",
  "firstname": "Марія",
  "phone": "+380935556677",
  "order_number": "В12345",
  "order_total": 2230,
  "discount": 250,
  "payment_method": "Онлайн оплата",
  "delivery_address": "НП 5, м. Київ",
  "delivery_cost": "Безкоштовна доставка",
  "items": [
    {
      "product_id": 1001,
      "name": "Сироватка",
      "image_url": "https://cosmy.com.ua/.../serum.jpg",
      "product_url": "https://cosmy.com.ua/...?utm_campaign=order",
      "price": 890,
      "price_old": 990,
      "quantity": 1
    },
    {
      "name": "Крем",
      "product_url": "https://cosmy.com.ua/.../cream",
      "image_url": "https://cosmy.com.ua/.../cream.jpg",
      "price": 1340,
      "quantity": 1
    }
  ]
}
```

Приклад через вкладений `order` + `totals` (якщо зручніше з OpenCart):

```json
{
  "email": "cosmy.ua@gmail.com",
  "name": "Марія",
  "order": {
    "order_number": "В12345",
    "payment_method": "Онлайн оплата",
    "delivery_address": "НП 5, м. Київ",
    "delivery_cost": "Безкоштовна доставка",
    "totals": [
      { "code": "sub_total", "value": 2480 },
      { "code": "coupon", "value": -250 },
      { "code": "total", "value": 2230 }
    ]
  },
  "items": [ ... ]
}
```

У листі: номер замовлення, товари, **знижка Cosmy club / промокод**, **«Разом»**, ПІБ/телефон, оплата, адреса, вартість доставки, блок про Telegram-бот.

Успішна відповідь додатково містить `order_number`, `order_total`, `discount`, `products_count`.

---

## 3) Покинули кошик

`POST /api/mailings/abandoned-cart`

**Тема:** `Можливо, Ви ще обираєте?`

```json
{
  "email": "cosmy.ua@gmail.com",
  "name": "Дарія",
  "cart_url": "https://cosmy.com.ua/check-ua?utm_source=email&utm_medium=trigger&utm_campaign=abandoned_cart",
  "items": [
    {
      "name": "Тонік",
      "image_url": "https://cosmy.com.ua/.../tonic.jpg",
      "product_url": "https://cosmy.com.ua/.../tonic?utm_campaign=abandoned_cart",
      "price": 650,
      "quantity": 1
    }
  ]
}
```

`cart_url` — опційно (для сумісності API); у листі перехід лише через `product_url` у товарах.

У `items[]` можна передати **будь-яку кількість товарів** — у листі компактний список як у листі замовлення (мініатюра, назва, кількість, ціна).

---

## 4) Покинули оплату

`POST /api/mailings/abandoned-payment`

**Тема:** `Оплату не завершено`

```json
{
  "email": "cosmy.ua@gmail.com",
  "name": "Дарія",
  "checkout_url": "https://cosmy.com.ua/index.php?route=checkout/checkout&utm_source=email&utm_campaign=abandoned_payment",
  "items": [
    {
      "name": "Сироватка",
      "image_url": "https://cosmy.com.ua/.../serum.jpg",
      "product_url": "https://cosmy.com.ua/.../serum?utm_campaign=abandoned_payment",
      "price": 890,
      "quantity": 1
    },
    {
      "name": "Тонік",
      "image_url": "https://cosmy.com.ua/.../tonic.jpg",
      "product_url": "https://cosmy.com.ua/.../tonic?utm_campaign=abandoned_payment",
      "price": 650,
      "quantity": 2
    }
  ]
}
```

- `checkout_url` — посилання на оплату з UTM (у листі: «Посилання на оплату: оплатити замовлення»);
- `items[]` — **опційно**: кілька товарів з кошика/замовлення (той самий компактний вигляд, що в листі замовлення). Без `items` — лише текст і посилання на оплату.

---

## 5) Зниження ціни — Wishlist

`POST /api/mailings/price-drop-wishlist`

**Тема:** `Ваш улюблений засіб тепер коштує менше ❤️`

```json
{
  "email": "cosmy.ua@gmail.com",
  "name": "Дарія",
  "wishlist_url": "https://cosmy.com.ua/index.php?route=account/wishlist&utm_campaign=price_drop_wishlist",
  "items": [
    {
      "name": "Сироватка",
      "image_url": "https://cosmy.com.ua/.../serum.jpg",
      "product_url": "https://cosmy.com.ua/.../serum?utm_campaign=price_drop",
      "old_price": 1490,
      "new_price": 1290
    }
  ]
}
```

Опційно `wishlist_url` з UTM — додаткова кнопка «До списку бажань».

---

## 6) Зниження ціни — товар у кошику

`POST /api/mailings/price-drop-cart`

**Тема:** `Товар, який ви обрали, тепер коштує менше ✨`

```json
{
  "email": "cosmy.ua@gmail.com",
  "name": "Дарія",
  "cart_url": "https://cosmy.com.ua/check-ua?utm_campaign=price_drop_cart",
  "items": [
    {
      "name": "Крем",
      "image_url": "https://cosmy.com.ua/.../cream.jpg",
      "product_url": "https://cosmy.com.ua/.../cream?utm_campaign=price_drop_cart",
      "old_price": 990,
      "new_price": 790
    },
    {
      "name": "Тонік",
      "product_url": "https://cosmy.com.ua/.../tonic",
      "image_url": "https://cosmy.com.ua/.../tonic.jpg",
      "old_price": 720,
      "new_price": 590
    }
  ]
}
```

`cart_url` — опційно (для сумісності API); у листі перехід через `product_url` і кнопку «Переглянути» в рядку товару.

---

## 7) Промокод підписки (popup на головній)

`POST /api/mailings/promo-signup`

Лист з персональним промокодом після підписки в попапі на cosmy.com.ua. Верстка — шаблон Stripo «Промо за підписку»; змінні підставляються з API.

**Тема (за замовч.):** `Ваш промокод −10% на Cosmy`

| Поле | Опис | У листі |
|------|------|---------|
| `email` | **Обов'язково.** Отримувач | — |
| `code` | **Обов'язково.** Промокод | великий код по центру |
| `store_url` | **Обов'язково.** Посилання на магазин | кнопка «Перейти до магазину» |
| `unsubscribe_url` | Опційно. Якщо не вказано — генерується автоматично (`/unsubscribe?t=…` на FinAdmin) | List-Unsubscribe header + посилання «Відписатися» |
| `lang` / `language` | Опційно (ігнорується). Лист лише українською | — |
| `discount_percent` | Розмір знижки, за замовч. `10` | «–10% на наступне замовлення» |
| `valid_hours` | Термін дії в годинах, за замовч. `24` | countdown GIF (від моменту відправки + N год) |
| `expires_at` | Опційно, для інтеграції; **на таймер не впливає** | — |
| `expires_at_tz` | TZ для `expires_at` | — |

Таймер — власний GIF (`/api/mailings/countdown.gif?ends=<unix_sec>`), стиль як у Stripo.  
Кінець = **момент відправки листа + `valid_hours`** (Europe/Kyiv).  
Stripo CDN не підтримує динамічні 24 год — рахує лише до кінця календарного дня.
| `min_total` | Мін. сума замовлення; якщо `> 0` — згадується в листі | під умовами промокоду |
| `external_request_id` | Ключ ідемпотентності | — |
| `template` / `template_id` | Опційно, зараз не використовується | — |
| `subject` | Опційно — перевизначити тему |

```json
{
  "template": "promo_signup",
  "email": "shopper@example.com",
  "lang": "uk",
  "language": "uk-ua",
  "external_request_id": "1234",
  "code": "CMK7QW2P",
  "discount_percent": 10,
  "valid_hours": 24,
  "expires_at": "2026-08-01 15:04:00",
  "expires_at_tz": "Europe/Kiev",
  "min_total": 0,
  "store_url": "https://cosmy.com.ua/",
  "unsubscribe_url": "https://cosmy.com.ua/index.php?route=promo/unsubscribe&t=9f2c1ab34de5678901234567890abcde"
}
```

**Відписка:** FinAdmin веде власний список відписаних (`/clients/mailings/unsubscribes`). Посилання в листі веде на `GET /index.php?route=promo/unsubscribe&t=…` на домені апки (`WEB_PUBLIC_BASE_URL`, напр. `https://cosmyfinances.telebots.site`). Заголовки `List-Unsubscribe` / `List-Unsubscribe-Post` підтримують one-click у Gmail. Відписані адреси не отримують маркетингові тригери та ручні розсилки; листи про замовлення не блокуються.

**Ідемпотентність:** повторний запит з тим самим `external_request_id` повертає `200` і той самий `message_id` (поле `deduplicated: true`).

---

## Відповіді

Успіх `200`:

```json
{
  "success": true,
  "sent": 1,
  "email": "client@example.com",
  "subject": "..."
}
```

Для **promo-signup** додатково:

```json
{
  "success": true,
  "sent": 1,
  "email": "shopper@example.com",
  "subject": "Ваш промокод −10% на Cosmy",
  "message_id": "abc-123",
  "code": "CMK7QW2P",
  "lang": "uk"
}
```

`message_id` — id відправки в Resend (для розбору інцидентів). Якщо id немає в відповіді провайдера, лист все одно вважається успішним.

Помилки: `400` (валідація), `401` (ключ), `422` / `502` (Resend).

Приклад помилки валідації email:

```json
{
  "success": false,
  "error": "The part after the @-sign is not valid. It is not within a valid top-level domain.",
  "error_code": "invalid_email"
}
```

Приклад помилки від Resend (невалідна адреса / відхилено провайдером):

```json
{
  "success": false,
  "error": "Invalid `to` field. The email address needs to follow the email@example.com format.",
  "error_code": "validation_error",
  "provider": "resend"
}
```

HTTP-статус відповіді збігається з кодом Resend для 4xx (наприклад `422`). Для 5xx від Resend — `502`.

**Важливо:** якщо Resend прийняв лист (`success: true`, є `message_id`), але адреса потім виявилась недоставною (bounce), це приходить **асинхронно** через webhooks Resend — не в синхронній відповіді API.

## curl (приклад promo-signup)

```bash
curl -X POST "https://YOUR-DOMAIN/api/mailings/promo-signup" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{
    "email": "shopper@example.com",
    "lang": "uk",
    "external_request_id": "1234",
    "code": "CMK7QW2P",
    "discount_percent": 10,
    "valid_hours": 24,
    "expires_at": "2026-08-01 15:04:00",
    "store_url": "https://cosmy.com.ua/",
    "unsubscribe_url": "https://cosmy.com.ua/index.php?route=promo/unsubscribe&t=sample"
  }'
```

## curl (приклад замовлення)

```bash
curl -X POST "https://YOUR-DOMAIN/api/mailings/order" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{
    "email": "cosmy.ua@gmail.com",
    "name": "Дарія",
    "order_number": "10482",
    "order_total": 2230,
    "discount": 250,
    "payment_method": "Картка",
    "delivery_address": "м. Київ, НП 1",
    "delivery_info": "Безкоштовна",
    "items": [{
      "name": "Сироватка",
      "price": 890,
      "quantity": 1,
      "image_url": "https://cosmy.com.ua/image/serum.jpg",
      "product_url": "https://cosmy.com.ua/serum?utm_source=email"
    }]
  }'
```

Колекція Postman: `settings/api/postman_mailings.json`.
