"""Cosmy trigger & marketing email HTML (Commissioner + Cormorant, minimal layout)."""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# Brand
COLOR_PRIMARY = "#A41D2E"
COLOR_TEXT = "#231F1F"
COLOR_MUTED = "#6B6565"
COLOR_BG = "#F7F5F4"
COLOR_CARD = "#FFFFFF"
COLOR_BORDER = "#E8E4E4"
SITE_URL = "https://cosmy.com.ua"
ACCOUNT_URL = "https://cosmy.com.ua/index.php?route=account/account"
TELEGRAM_BOT_URL = "https://t.me/Cosmy_bot"
CART_URL_DEFAULT = "https://cosmy.com.ua/check-ua"
CHECKOUT_URL_DEFAULT = "https://cosmy.com.ua/index.php?route=checkout/checkout"
WISHLIST_URL_DEFAULT = "https://cosmy.com.ua/index.php?route=account/wishlist"

# Лого: PNG @2x (SVG у поштових клієнтах часто розмитий)
LOGO_SRC = "/static/cosmy25@2x.png"
LOGO_WIDTH = 180
LOGO_HEIGHT = 26
FOOTER_LOGO_SRC = "/static/email/cosmy-logo-white.png"
FOOTER_LOGO_WIDTH = 153  # ~15% менше за хедерне лого
TELEGRAM_BOT_IMAGE_SRC = "/static/email/telegram-bot-promo.png"
TELEGRAM_ICON_SRC = "/static/email/telegram-circle-white.png"
LINE_BODY = "1.45"
LINE_BODY_TIGHT = "1.35"
# Єдина шкала відступів для всіх листів
SPACE_XS = 8
SPACE_SM = 12
SPACE_MD = 16
SPACE_LG = 24
SPACE_XL = 28
SPACE_XXL = 32
PARAGRAPH_GAP = SPACE_LG
GREETING_GAP = SPACE_LG
CONTENT_PADDING = "16px 40px 48px"
CONTENT_PADDING_MOBILE = "16px 16px 40px"
EMAIL_MOBILE_CSS = """
    @media only screen and (max-width: 620px) {{
      .email-container {{
        width: 100% !important;
        max-width: 100% !important;
      }}
      .email-content-pad {{
        padding: {mobile_content} !important;
      }}
      .email-header-pad {{
        padding: 28px 16px 24px !important;
      }}
      .email-footer-pad {{
        padding: 24px 16px 32px !important;
      }}
      .email-box-pad {{
        padding-left: 14px !important;
        padding-right: 14px !important;
      }}
      .email-order-card-pad {{
        padding: 12px 10px !important;
      }}
      .email-product-card-pad {{
        padding: 16px 14px 18px !important;
      }}
      .email-display-title {{
        font-size: 24px !important;
        line-height: 1.25 !important;
      }}
      .email-order-img-col {{
        padding-right: 10px !important;
      }}
    }}
""".format(mobile_content=CONTENT_PADDING_MOBILE)
SECTION_SPACER = SPACE_XXL
CLOSING_TOP = SPACE_XXL
BTN_BLOCK_MARGIN = f"{SPACE_XL}px auto {SPACE_XXL}px"
ORDER_LINE_ROW_GAP = SPACE_LG
ORDER_LINE_INNER = SPACE_SM
ORDER_SUMMARY_META_PAD = SPACE_LG
PRODUCTS_BLOCK_MARGIN = f"{SPACE_MD}px 0 {SPACE_LG}px"

GOOGLE_FONTS_LINK = (
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Commissioner:wght@400;500;600&family=Cormorant:wght@500;600&display=swap" '
    'rel="stylesheet">'
)
GOOGLE_FONTS_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Commissioner:wght@400;500;600&family=Cormorant:wght@500;600&display=swap');"
)
# Gmail дозволяє @font-face лише з fonts.gstatic.com (не з нашого домену).
_GSTATIC_COMMISSIONER_CYR = (
    "https://fonts.gstatic.com/s/commissioner/v24/"
    "tDbw2o2WnlgI0FNDgduEk4jAhwgumbU1SVfU5BD8OuRL8OstC6KOhgvBYWSFJ-Mgdrgiju6fF8m0bkXaexs.woff2"
)
_GSTATIC_COMMISSIONER_LAT = (
    "https://fonts.gstatic.com/s/commissioner/v24/"
    "tDbw2o2WnlgI0FNDgduEk4jAhwgumbU1SVfU5BD8OuRL8OstC6KOhgvBYWSFJ-Mgdrgiju6fF8m0akXa.woff2"
)
_GSTATIC_CORMORANT_CYR = (
    "https://fonts.gstatic.com/s/cormorant/v24/H4clBXOCl9bbnla_nHIq65u9uqc.woff2"
)
_GSTATIC_CORMORANT_LAT = (
    "https://fonts.gstatic.com/s/cormorant/v24/H4clBXOCl9bbnla_nHIq75u9.woff2"
)


def build_email_font_face_css() -> str:
    """Web fonts for email clients (Gmail web / Apple Mail). Hosted on fonts.gstatic.com."""
    comm_src = f"url('{_GSTATIC_COMMISSIONER_CYR}') format('woff2'), url('{_GSTATIC_COMMISSIONER_LAT}') format('woff2')"
    corm_src = f"url('{_GSTATIC_CORMORANT_CYR}') format('woff2'), url('{_GSTATIC_CORMORANT_LAT}') format('woff2')"
    faces: list[str] = []
    for weight in (400, 500, 600):
        faces.append(
            f"""    @font-face {{
      font-family: 'Commissioner';
      font-style: normal;
      font-weight: {weight};
      src: {comm_src};
    }}"""
        )
    for weight in (500, 600):
        faces.append(
            f"""    @font-face {{
      font-family: 'Cormorant';
      font-style: normal;
      font-weight: {weight};
      src: {corm_src};
    }}"""
        )
    return "@media screen {\n" + "\n".join(faces) + "\n    }"


def get_font_patch() -> str:
    """Link + @font-face block to inject into <head> (safe to re-apply on every send)."""
    return (
        GOOGLE_FONTS_LINK
        + '\n  <style type="text/css">\n    '
        + GOOGLE_FONTS_IMPORT
        + "\n    "
        + build_email_font_face_css()
        + "\n  </style>"
    )


def _strip_email_font_blocks(content: str) -> str:
    content = re.sub(r'<link[^>]*fonts\.googleapis\.com[^>]*>', "", content, flags=re.IGNORECASE)
    content = re.sub(
        r"@import\s+url\(\s*['\"]?https://fonts\.googleapis\.com[^)]+\)\s*;?",
        "",
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(
        r"@font-face\s*\{[^}]*\}",
        "",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    content = re.sub(
        r"@font-face\s*\{[^}]*data:font/woff2[^}]*\}",
        "",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return content


def ensure_brand_fonts_in_html(raw_html: str) -> str:
    """Normalize font sources before send (Gmail ignores self-hosted and base64 fonts)."""
    content = str(raw_html or "")
    if not content.strip():
        return content
    if not re.search(r"<head\b", content, re.IGNORECASE):
        return content
    content = _strip_email_font_blocks(content)
    patch = get_font_patch()
    return re.sub(
        r"(<head[^>]*>)",
        r"\1\n  " + patch,
        content,
        count=1,
        flags=re.IGNORECASE,
    )


def get_font_head() -> str:
    return """
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <meta name="x-apple-disable-message-reformatting">
  """ + get_font_patch() + """
  <style type="text/css">
    html, body { margin: 0 !important; padding: 0 !important; width: 100% !important; min-height: 100% !important; background: #FFFFFF !important; font-family: """ + FONT_BODY + """ !important; }
    .email-root, .email-shell { width: 100% !important; background: #FFFFFF !important; }
    .email-container { width: 100%; max-width: 600px; }
    .email-display-title { font-family: """ + FONT_DISPLAY + """ !important; }
    @media (prefers-color-scheme: dark) {
      html, body, .email-root, .email-shell { background: #FFFFFF !important; color: #231F1F !important; }
    }
    """ + EMAIL_MOBILE_CSS + """
  </style>
"""
FONT_BODY = "'Commissioner', Arial, Helvetica, sans-serif"
FONT_DISPLAY = "'Cormorant', Georgia, 'Times New Roman', serif"


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def format_price_ua(price: float | int | None, *, allow_zero: bool = False) -> str:
    if price is None:
        return ""
    try:
        n = float(price)
    except (TypeError, ValueError):
        return ""
    if n < 0:
        return ""
    if n == 0 and not allow_zero:
        return ""
    return f"{n:,.0f}".replace(",", " ") + " грн"


def _greeting_name(name: str | None) -> str:
    n = str(name or "").strip()
    if not n:
        return "Вітаємо"
    return _esc(n)


def _logo_block() -> str:
    return f"""
    <tr>
      <td align="center" class="email-header-pad" style="padding:36px 32px 32px;background:{COLOR_CARD};">
        <a href="{SITE_URL}" style="text-decoration:none;">
          <img src="{LOGO_SRC}" alt="Cosmy" width="{LOGO_WIDTH}" height="{LOGO_HEIGHT}"
            style="display:block;margin:0 auto;border:0;width:{LOGO_WIDTH}px;max-width:{LOGO_WIDTH}px;height:auto;">
        </a>
      </td>
    </tr>"""


def _footer_block(*, show_center_logo: bool = False) -> str:
    logo_row = ""
    if show_center_logo:
        logo_row = f"""
          <tr>
            <td align="center" style="padding:0 0 16px;">
              <a href="{SITE_URL}" style="text-decoration:none;">
                <img src="{FOOTER_LOGO_SRC}" alt="Cosmy" width="{FOOTER_LOGO_WIDTH}"
                  style="display:block;margin:0 auto;border:0;width:{FOOTER_LOGO_WIDTH}px;max-width:{FOOTER_LOGO_WIDTH}px;height:auto;">
              </a>
            </td>
          </tr>"""
    footer_pad = "16px 32px 40px" if show_center_logo else "36px 32px 40px"
    return f"""
    <tr>
      <td class="email-footer-pad" style="padding:{footer_pad};background:{COLOR_PRIMARY};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          {logo_row}
          <tr>
            <td align="center" style="padding-bottom:16px;">
              <a href="{SITE_URL}" style="font-family:{FONT_BODY};font-size:12px;color:#FFFFFF;text-decoration:none;margin:0 10px;">Магазин</a>
              <a href="{ACCOUNT_URL}" style="font-family:{FONT_BODY};font-size:12px;color:#FFFFFF;text-decoration:none;margin:0 10px;">Особистий кабінет</a>
              <a href="https://www.instagram.com/cosmy/" style="font-family:{FONT_BODY};font-size:12px;color:#FFFFFF;text-decoration:none;margin:0 10px;">Instagram</a>
            </td>
          </tr>
          <tr>
            <td align="center" style="font-family:{FONT_BODY};font-size:11px;line-height:1.6;color:rgba(255,255,255,0.85);">
              © 2026 Cosmy.com.ua
            </td>
          </tr>
        </table>
      </td>
    </tr>"""


def _btn_primary(label: str, href: str) -> str:
    safe_label = _esc(label)
    safe_href = _esc(href)
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:{BTN_BLOCK_MARGIN};">
      <tr>
        <td align="center" style="border-radius:999px;background:{COLOR_PRIMARY};">
          <a href="{safe_href}" target="_blank"
            style="display:inline-block;padding:14px 32px;font-family:{FONT_BODY};font-size:13px;font-weight:600;
            color:#FFFFFF;text-decoration:none;border-radius:999px;letter-spacing:0.02em;">
            {safe_label}
          </a>
        </td>
      </tr>
    </table>"""


def _btn_telegram(label: str, href: str) -> str:
    """Яскрава CTA-кнопка Telegram: червона заливка, білий текст і іконка."""
    safe_label = _esc(label)
    safe_href = _esc(href)
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"
      style="margin:{SPACE_XL}px auto 0;">
      <tr>
        <td align="center" bgcolor="{COLOR_PRIMARY}"
          style="background:{COLOR_PRIMARY};border-radius:999px;mso-padding-alt:0;">
          <a href="{safe_href}" target="_blank"
            style="display:inline-block;padding:16px 30px;font-family:{FONT_BODY};font-size:14px;font-weight:600;
            color:#FFFFFF;text-decoration:none;border-radius:999px;letter-spacing:0.04em;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center">
              <tr>
                <td style="vertical-align:middle;font-family:{FONT_BODY};font-size:14px;font-weight:600;
                  color:#FFFFFF;letter-spacing:0.04em;">{safe_label}</td>
                <td style="padding-left:10px;vertical-align:middle;line-height:0;">
                  <img src="{_esc(TELEGRAM_ICON_SRC)}" alt="" width="20" height="20"
                    style="display:block;width:20px;height:20px;border:0;">
                </td>
              </tr>
            </table>
          </a>
        </td>
      </tr>
    </table>"""


def _btn_primary_inline(label: str, href: str) -> str:
    """Компактна кнопка в рядку товару (без великих відступів по центру)."""
    safe_label = _esc(label)
    safe_href = _esc(href)
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:{SPACE_MD}px 0 0;">
      <tr>
        <td align="left" style="border-radius:999px;background:{COLOR_PRIMARY};">
          <a href="{safe_href}" target="_blank"
            style="display:inline-block;padding:10px 20px;font-family:{FONT_BODY};font-size:12px;font-weight:600;
            color:#FFFFFF;text-decoration:none;border-radius:999px;letter-spacing:0.02em;">
            {safe_label}
          </a>
        </td>
      </tr>
    </table>"""


def _paragraph(text: str, *, margin_bottom: int | None = None) -> str:
    gap = PARAGRAPH_GAP if margin_bottom is None else margin_bottom
    lines = [ln.strip() for ln in str(text or "").split("\n") if ln.strip()]
    if not lines:
        return ""
    parts = []
    for ln in lines:
        parts.append(
            f'<p style="margin:0 0 {gap}px;font-family:{FONT_BODY};font-size:15px;'
            f'line-height:{LINE_BODY};color:{COLOR_TEXT};">{_esc(ln)}</p>'
        )
    return "\n".join(parts)


def _heading(text: str) -> str:
    return f"""
    <p class="email-display-title" style="margin:0 0 {SPACE_XL}px;font-family:{FONT_DISPLAY};font-size:28px;line-height:1.3;font-weight:600;color:{COLOR_TEXT};">
      {_esc(text)}
    </p>"""


def _greeting_line(name: str | None) -> str:
    """Імʼя отримувача — легше за заголовок, не вибивається з тексту."""
    g = _greeting_name(name)
    return (
        f'<p style="margin:0 0 {GREETING_GAP}px;font-family:{FONT_BODY};font-size:20px;'
        f'line-height:{LINE_BODY_TIGHT};font-weight:400;color:{COLOR_TEXT};">{g},</p>'
    )


def _closing_line(inner_html: str) -> str:
    return (
        f'<p style="margin:{CLOSING_TOP}px 0 0;font-family:{FONT_BODY};font-size:15px;'
        f'line-height:{LINE_BODY};color:{COLOR_TEXT};">{inner_html}</p>'
    )


def _products_block_html(inner: str) -> str:
    """Обгортка списку товарів з рівномірними відступами зверху/знизу."""
    if not str(inner or "").strip():
        return ""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:{PRODUCTS_BLOCK_MARGIN};"><tr><td>{inner}</td></tr></table>'
    )


def _section_spacer(height_px: int = SECTION_SPACER) -> str:
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="height:{height_px}px;font-size:0;line-height:0;">&nbsp;</td></tr></table>'
    )


def _bullet_list(items: list[str]) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<tr><td style="padding:0 0 {SPACE_SM}px 0;font-family:{FONT_BODY};font-size:15px;line-height:{LINE_BODY};color:{COLOR_TEXT};">'
        f'<span style="color:{COLOR_PRIMARY};padding-right:{SPACE_XS}px;">•</span>{_esc(item)}</td></tr>'
        for item in items
    )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 {SPACE_XL}px;">{rows}</table>'


def _wrap_email(
    title: str,
    body_rows: str,
    *,
    lang: str = "uk",
    footer_center_logo: bool = False,
) -> str:
    safe_title = _esc(title)
    html_lang = "uk" if str(lang or "").lower().startswith("uk") else "ru"
    return f"""<!DOCTYPE html>
<html lang="{html_lang}" style="margin:0;padding:0;background:{COLOR_CARD};">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
  {get_font_head()}
</head>
<body class="email-root" style="margin:0;padding:0;background:{COLOR_CARD};font-family:{FONT_BODY};" bgcolor="{COLOR_CARD}">
  <table role="presentation" class="email-shell" width="100%" cellpadding="0" cellspacing="0" border="0"
    style="width:100%;background:{COLOR_CARD};" bgcolor="{COLOR_CARD}">
    <tr>
      <td align="center" style="padding:0;background:{COLOR_CARD};" bgcolor="{COLOR_CARD}">
        <table role="presentation" class="email-container" width="600" cellpadding="0" cellspacing="0" border="0"
          style="max-width:600px;width:100%;background:{COLOR_CARD};" bgcolor="{COLOR_CARD}">
          {_logo_block()}
          {body_rows}
          {_footer_block(show_center_logo=footer_center_logo)}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _content_section(inner_html: str) -> str:
    return f"""
    <tr>
      <td class="email-content-pad" style="padding:{CONTENT_PADDING};background:{COLOR_CARD};">
        {inner_html}
      </td>
    </tr>"""


def _product_href(p: dict[str, Any]) -> str:
    return str(p.get("product_url") or SITE_URL).strip() or SITE_URL


def _email_link(href: str, inner_html: str) -> str:
    safe_href = _esc((href or SITE_URL).strip() or SITE_URL)
    return (
        f'<a href="{safe_href}" target="_blank" '
        f'style="color:inherit;text-decoration:none;">{inner_html}</a>'
    )


def _product_price_line_html(p: dict[str, Any], *, align_right: bool = False) -> str:
    price = p.get("price")
    price_old = p.get("price_old")
    price_text = format_price_ua(price)
    align = "text-align:right;" if align_right else ""
    if not price_text:
        return ""
    if price_old and price and float(price_old) > float(price):
        return (
            f'<p style="margin:0;font-family:{FONT_BODY};font-size:15px;line-height:{LINE_BODY};{align}">'
            f'<span style="font-weight:600;color:{COLOR_PRIMARY};">{_esc(price_text)}</span>'
            f'<br><span style="font-size:12px;color:{COLOR_MUTED};text-decoration:line-through;">'
            f'{_esc(format_price_ua(price_old))}</span></p>'
        )
    return (
        f'<p style="margin:0;font-family:{FONT_BODY};font-size:15px;line-height:{LINE_BODY};'
        f'font-weight:600;color:{COLOR_PRIMARY};{align}">{_esc(price_text)}</p>'
    )


def _order_qty_badge(qty_label: str) -> str:
    return (
        f'<span style="display:inline-block;padding:5px 11px;background:{COLOR_BG};border-radius:999px;'
        f'font-family:{FONT_BODY};font-size:12px;line-height:1;font-weight:500;color:{COLOR_MUTED};'
        f'white-space:nowrap;">{_esc(qty_label)}</span>'
    )


def _order_line_row(p: dict[str, Any]) -> str:
    href = _product_href(p)
    safe_name = _esc(p.get("name") or "Товар")
    safe_img = _esc(str(p.get("image_url") or "").strip() or LOGO_SRC)
    try:
        qty_n = max(1, int(p.get("quantity") or 1))
    except (TypeError, ValueError):
        qty_n = 1
    qty_label = f"{qty_n} шт."
    price_html = _product_price_line_html(p, align_right=True)
    name_cell = _email_link(
        href,
        f'<span style="color:{COLOR_TEXT};text-decoration:none;">{safe_name}</span>',
    )
    img_cell = _email_link(
        href,
        f'<img src="{safe_img}" alt="{safe_name}" width="88" height="88" '
        f'style="display:block;width:88px;height:88px;object-fit:cover;border-radius:12px;border:0;'
        f'background:#F3F0EF;border:1px solid {COLOR_BORDER};">',
    )
    return f"""
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
              style="margin-bottom:{SPACE_MD}px;background:{COLOR_CARD};border:1px solid {COLOR_BORDER};border-radius:14px;">
              <tr>
                <td class="email-order-card-pad" style="padding:14px 16px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td class="email-order-img-col" width="88" valign="top" style="padding-right:14px;line-height:0;">{img_cell}</td>
                      <td valign="top" style="font-family:{FONT_BODY};font-size:14px;line-height:{LINE_BODY};color:{COLOR_TEXT};">
                        <p style="margin:0 0 12px;font-size:15px;line-height:1.4;font-weight:500;word-break:break-word;overflow-wrap:anywhere;">{name_cell}</p>
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                          <tr>
                            <td valign="bottom" style="padding-top:2px;white-space:nowrap;">{_order_qty_badge(qty_label)}</td>
                            <td align="right" valign="bottom" style="padding-left:12px;white-space:nowrap;">{price_html}</td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>"""


def product_card_linked_html(p: dict[str, Any], *, cta_label: str = "Переглянути") -> str:
    """Картка товару: клікабельні фото, назва та кнопка."""
    href = _product_href(p)
    safe_name = _esc(p.get("name") or "Товар")
    safe_img = _esc(str(p.get("image_url") or "").strip() or LOGO_SRC)
    name_link = _email_link(href, safe_name)
    img_link = _email_link(
        href,
        f'<img src="{safe_img}" alt="{safe_name}" width="280" height="280" '
        f'style="display:block;width:100%;max-width:100%;height:auto;border:0;object-fit:cover;">',
    )
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
      style="background:{COLOR_CARD};border:1px solid {COLOR_BORDER};border-radius:16px;overflow:hidden;margin-bottom:{ORDER_LINE_ROW_GAP}px;">
      <tr>
        <td style="padding:0;background:#F3F0EF;line-height:0;">{img_link}</td>
      </tr>
      <tr>
        <td class="email-product-card-pad" style="padding:20px 22px 22px;font-family:{FONT_BODY};font-size:15px;color:{COLOR_TEXT};">
          <p style="margin:0 0 {SPACE_SM}px;font-family:{FONT_BODY};font-size:15px;line-height:{LINE_BODY};font-weight:500;">{name_link}</p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
            style="margin:0 0 {SPACE_MD}px;"><tr><td>{_product_price_line_html(p)}</td></tr></table>
          {_btn_primary_inline(cta_label, href)}
        </td>
      </tr>
    </table>"""


def products_catalog_linked_html(products: list[dict[str, Any]]) -> str:
    if not products:
        return ""
    return _products_block_html("".join(product_card_linked_html(p) for p in products))


def products_order_lines_html(products: list[dict[str, Any]]) -> str:
    """Компактний список товарів як у листі замовлення (без підсумку та доставки)."""
    if not products:
        return ""
    return _products_block_html("".join(_order_line_row(p) for p in products))


def _subheading(text: str) -> str:
    return (
        f'<p style="margin:0 0 {SPACE_MD}px;font-family:{FONT_BODY};font-size:17px;'
        f'line-height:{LINE_BODY};font-weight:600;color:{COLOR_TEXT};">{_esc(text)}</p>'
    )


def _plain_detail_line(text: str, *, bold: bool = False) -> str:
    if not str(text or "").strip():
        return ""
    weight = "font-weight:600;" if bold else ""
    return (
        f'<p style="margin:0 0 {SPACE_SM}px;font-family:{FONT_BODY};font-size:15px;'
        f'line-height:{LINE_BODY};color:{COLOR_TEXT};{weight}">{_esc(text)}</p>'
    )


def _order_meta_row(label: str, value: str) -> str:
    if not value:
        return ""
    return (
        f'<tr><td style="padding:12px 0;font-family:{FONT_BODY};font-size:14px;line-height:1.45;">'
        f'<span style="color:{COLOR_MUTED};">{_esc(label)}:</span> '
        f'<span style="color:{COLOR_TEXT};font-weight:500;">{_esc(value)}</span></td></tr>'
    )


def _order_delivery_details_block(order_ctx: dict[str, Any]) -> str:
    full_name = str(order_ctx.get("customer_full_name") or "").strip()
    phone = str(order_ctx.get("phone") or "").strip()
    payment = str(order_ctx.get("payment_method") or "").strip()
    address = str(order_ctx.get("delivery_address") or "").strip()
    delivery_cost = str(
        order_ctx.get("delivery_cost") or order_ctx.get("delivery_info") or ""
    ).strip()

    inner_parts: list[str] = []
    if full_name:
        inner_parts.append(_plain_detail_line(full_name, bold=True))
    if phone:
        inner_parts.append(_plain_detail_line(phone))
    meta_rows = "".join(
        [
            _order_meta_row("Спосіб оплати", payment),
            _order_meta_row("Адреса доставки", address),
            _order_meta_row("Вартість доставки", delivery_cost),
        ]
    )
    if meta_rows:
        inner_parts.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin-top:{SPACE_SM}px;">{meta_rows}</table>'
        )
    if not inner_parts:
        return ""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin-top:{SPACE_XL}px;background:{COLOR_BG};border-radius:12px;">'
        f'<tr><td class="email-box-pad" style="padding:{ORDER_SUMMARY_META_PAD}px 20px;">{"".join(inner_parts)}</td></tr></table>'
    )


def _telegram_bot_promo_block() -> str:
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
      style="margin:{SPACE_XXL}px 0 0;">
      <tr>
        <td align="center" style="padding:0 0 {SPACE_LG}px;line-height:0;">
          <img src="{TELEGRAM_BOT_IMAGE_SRC}" alt="Cosmy Telegram bot" width="520"
            style="display:block;width:100%;max-width:520px;height:auto;border:0;margin:0 auto;">
        </td>
      </tr>
      <tr>
        <td style="font-family:{FONT_BODY};color:{COLOR_TEXT};">
          {_paragraph("Ваше замовлення завжди під рукою у нашому чат-бот Telegram.")}
          {_subheading("Що вміє Cosmy bot?")}
          {_bullet_list([
              "Потрібна консультація у підборі засобів собі чи близьким?",
              "Хочеш дізнатись статус свого замовлення?",
              "Цікавлять терміни доставки та види оплати?",
              "Хочеш переглянути історію своїх замовлень?",
          ])}
          {_closing_line(
              'Cosmy bot допоможе знайти відповіді на ці та багато інших питань, '
              f'заощадить твій час і зробить покупки ще зручнішими <span style="color:{COLOR_PRIMARY};">❤️</span>'
          )}
          {_btn_telegram("Telegram Chat Bot", TELEGRAM_BOT_URL)}
        </td>
      </tr>
    </table>"""


def _order_totals_row(label: str, value_html: str, *, last: bool = False) -> str:
    pad = "16px 0 0" if last else "0 0 10px"
    return (
        f'<tr><td style="padding:{pad};font-family:{FONT_BODY};font-size:15px;line-height:{LINE_BODY};">'
        f'<span style="color:{COLOR_MUTED};">{_esc(label)}</span> {value_html}</td></tr>'
    )


def _order_totals_block(order_ctx: dict[str, Any]) -> str:
    """Блок як у кошику: знижка Cosmy club/промокод + «Разом» (сума замовлення)."""
    discount = order_ctx.get("discount")
    total = order_ctx.get("order_total")
    rows: list[str] = []
    try:
        disc_n = float(discount) if discount is not None else 0.0
    except (TypeError, ValueError):
        disc_n = 0.0
    if disc_n > 0:
        disc_text = format_price_ua(disc_n, allow_zero=False)
        if disc_text:
            rows.append(
                _order_totals_row(
                    "Знижка Cosmy club / Промокод:",
                    f'<span style="color:{COLOR_TEXT};font-weight:500;">−{_esc(disc_text)}</span>',
                )
            )
    if total is not None:
        total_text = format_price_ua(total, allow_zero=True)
        if total_text:
            rows.append(
                _order_totals_row(
                    "Разом:",
                    f'<strong style="color:{COLOR_PRIMARY};">{_esc(total_text)}</strong>',
                    last=bool(rows),
                )
            )
    if not rows:
        return ""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:{SPACE_XL}px 0 0;">{"".join(rows)}</table>'
    )


def order_summary_html(products: list[dict[str, Any]], order_ctx: dict[str, Any]) -> str:
    if not products and not any(
        order_ctx.get(k)
        for k in (
            "order_total",
            "discount",
            "order_number",
            "payment_method",
            "delivery_address",
            "delivery_info",
            "delivery_cost",
            "customer_full_name",
            "phone",
        )
    ):
        return ""
    parts: list[str] = [_section_spacer()]
    order_no = str(order_ctx.get("order_number") or "").strip()
    if order_no:
        parts.append(
            f'<p style="margin:0 0 {SPACE_LG}px;font-family:{FONT_BODY};font-size:15px;'
            f'line-height:{LINE_BODY};font-weight:600;color:{COLOR_PRIMARY};">'
            f"Замовлення: {_esc(order_no)}</p>"
        )
    if products:
        for p in products:
            parts.append(_order_line_row(p))
    totals_html = _order_totals_block(order_ctx)
    if totals_html:
        parts.append(totals_html)
    delivery_block = _order_delivery_details_block(order_ctx)
    if delivery_block:
        parts.append(delivery_block)
    parts.append(_section_spacer())
    return "".join(parts)


def render_welcome_email(name: str | None = None) -> tuple[str, str]:
    subject = "Раді бачити Вас у Cosmy"
    body = _content_section(
        f"""
        {_greeting_line(name)}
        {_paragraph("Вітаємо у Cosmy!")}
        {_paragraph("Ми створили простір, у якому косметика — це не шум із тисячі банок.")}
        {_paragraph("Це зрозумілий вибір під Ваші потреби.")}
        {_paragraph("Тут ми:")}
        {_bullet_list([
            "відбираємо лише те, що дійсно працює",
            "пояснюємо складне просто",
            "допомагаємо не купувати зайве",
            "дбаємо про комфорт Вашої рутини",
        ])}
        {_paragraph("Якщо не знаєте, з чого почати — ми поруч, щоб допомогти підібрати догляд.")}
        {_paragraph("Без нав'язаних рішень.")}
        {_closing_line(f'Бажаємо Вам приємного знайомства з Cosmy <span style="color:{COLOR_PRIMARY};">❤️</span>')}
        """
    )
    return subject, _wrap_email(subject, body)


def render_order_placed_email(
    name: str | None,
    products: list[dict[str, Any]],
    order_ctx: dict[str, Any] | None = None,
) -> tuple[str, str]:
    ctx = dict(order_ctx or {})
    if not ctx.get("customer_full_name") and str(name or "").strip():
        ctx["customer_full_name"] = str(name).strip()

    subject = "Ваше замовлення успішно прийнято та передано в роботу ✨"
    if str(name or "").strip():
        opening = f"{_greeting_name(name)}, Ваше замовлення успішно прийнято та передано в роботу ✨"
    else:
        opening = "Ваше замовлення успішно прийнято та передано в роботу ✨"
    body = _content_section(
        f"""
        {_paragraph(opening, margin_bottom=SPACE_LG)}
        {order_summary_html(products, ctx)}
        {_paragraph("Щойно замовлення буде відправлене, Ви отримаєте окреме повідомлення з деталями доставки.")}
        {_paragraph("За потреби уточнення деталей замовлення Ваш персональний асистент обов'язково зв'яжеться з Вами.")}
        {_closing_line(f'Дякуємо, що обираєте Cosmy <span style="color:{COLOR_PRIMARY};">❤️</span>')}
        {_telegram_bot_promo_block()}
        {_closing_line(f'Cosmy — турбота в кожній покупці <span style="color:{COLOR_PRIMARY};">❤️</span>')}
        """
    )
    return subject, _wrap_email(subject, body, footer_center_logo=True)


def render_abandoned_cart_email(
    name: str | None,
    products: list[dict[str, Any]],
    cart_url: str | None = None,
) -> tuple[str, str]:
    subject = "Можливо, Ви ще обираєте?"
    catalog = products_order_lines_html(products)
    body = _content_section(
        f"""
        {_greeting_line(name)}
        {_paragraph("Ви додали товари до кошика, але не завершили замовлення.")}
        {_paragraph("І це нормально. Хороший догляд — не про імпульсивні покупки, а про рішення, у яких Ви впевнені.")}
        {_paragraph("Ми зберегли Ваш вибір, щоб Ви могли повернутися до нього у зручний момент.")}
        {catalog}
        {_paragraph("Якщо залишилися сумніви щодо текстур, активів чи поєднання засобів — Cosmy допоможе розібратися без зайвого шуму та складних обіцянок.")}
        {_closing_line(f'Ваш кошик чекатиме на Вас <span style="color:{COLOR_PRIMARY};">❤️</span>')}
        """
    )
    return subject, _wrap_email(subject, body)


def _price_drop_savings_label(old_price: float | int | None, new_price: float | int | None) -> str:
    try:
        old_n = float(old_price) if old_price is not None else 0.0
        new_n = float(new_price) if new_price is not None else 0.0
    except (TypeError, ValueError):
        return "Ціна знизилась"
    if old_n > new_n > 0:
        pct = int(round((1 - new_n / old_n) * 100))
        if pct > 0:
            return f"−{pct}%"
    return "Ціна знизилась"


def product_card_price_drop_html(
    p: dict[str, Any],
    *,
    old_label: str = "Стара ціна",
    new_label: str = "Нова ціна",
) -> str:
    """Рядок зниження як у замовленні: мініатюра, назва, було/стало, «Переглянути»."""
    href = _product_href(p)
    safe_name = _esc(p.get("name") or "Товар")
    safe_img = _esc(str(p.get("image_url") or "").strip() or LOGO_SRC)
    old_p = p.get("old_price")
    new_p = p.get("new_price") if p.get("new_price") is not None else p.get("price")
    old_text = format_price_ua(old_p)
    new_text = format_price_ua(new_p)
    name_cell = _email_link(href, safe_name)
    img_cell = _email_link(
        href,
        f'<img src="{safe_img}" alt="{safe_name}" width="72" height="72" '
        f'style="display:block;width:72px;height:72px;object-fit:cover;border-radius:12px;border:0;background:#F3F0EF;">',
    )
    old_html = (
        f'<p style="margin:0 0 {SPACE_XS}px;font-family:{FONT_BODY};font-size:13px;color:{COLOR_MUTED};">'
        f'{_esc(old_label)}: <span style="text-decoration:line-through;">{_esc(old_text)}</span></p>'
        if old_text
        else ""
    )
    new_html = (
        f'<p style="margin:0;font-family:{FONT_BODY};font-size:14px;line-height:{LINE_BODY};color:{COLOR_TEXT};">'
        f'{_esc(new_label)}: <strong style="color:{COLOR_PRIMARY};">{_esc(new_text)}</strong></p>'
        if new_text
        else ""
    )
    return f"""
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
              style="margin-bottom:{ORDER_LINE_ROW_GAP}px;border-bottom:1px solid {COLOR_BORDER};padding-bottom:{ORDER_LINE_ROW_GAP}px;">
              <tr>
                <td width="72" valign="top" style="padding-right:{SPACE_MD}px;line-height:0;">{img_cell}</td>
                <td valign="top" style="font-family:{FONT_BODY};font-size:14px;line-height:{LINE_BODY};color:{COLOR_TEXT};padding-right:{SPACE_XS}px;">
                  <p style="margin:0 0 {ORDER_LINE_INNER}px;font-weight:500;">{name_cell}</p>
                  {old_html}
                  {new_html}
                  {_btn_primary_inline("Переглянути", href)}
                </td>
              </tr>
            </table>"""


def products_price_drop_html(
    products: list[dict[str, Any]],
    *,
    old_label: str = "Стара ціна",
    new_label: str = "Нова ціна",
) -> str:
    if not products:
        return ""
    rows = "".join(
        product_card_price_drop_html(p, old_label=old_label, new_label=new_label) for p in products
    )
    return _products_block_html(rows)


def render_price_drop_wishlist_email(
    name: str | None,
    products: list[dict[str, Any]],
    wishlist_url: str | None = None,
) -> tuple[str, str]:
    subject = "Ваш улюблений засіб тепер коштує менше ❤️"
    url = (wishlist_url or "").strip() or None
    catalog = products_price_drop_html(products, old_label="Стара ціна", new_label="Нова ціна")
    extra_btn = _btn_primary("До списку бажань", url) if url else ""
    body = _content_section(
        f"""
        {_greeting_line(name)}
        {_paragraph("Маємо приємну новину 💫")}
        {_paragraph("Один із засобів, який Ви зберегли або розглядали раніше, зараз можна придбати за нижчою ціною.")}
        {catalog}
        {extra_btn}
        {_paragraph("Якщо цей засіб усе ще у Вашому списку бажань, можливо, зараз саме той момент, коли варто повернутися до нього.")}
        """
    )
    return subject, _wrap_email(subject, body)


def render_price_drop_cart_email(
    name: str | None,
    products: list[dict[str, Any]],
    cart_url: str | None = None,
) -> tuple[str, str]:
    subject = "Товар, який ви обрали, тепер коштує менше ✨"
    catalog = products_price_drop_html(products, old_label="Було", new_label="Стало")
    body = _content_section(
        f"""
        {_greeting_line(name)}
        {_paragraph("Товар, який залишився у Вашому кошику, зараз доступний за вигіднішою ціною!")}
        {catalog}
        {_paragraph("Можливо, Ви ще порівнювали варіанти, знайомилися зі складом або просто залишили час на роздуми.")}
        {_paragraph("Якщо цей засіб усе ще відповідає Вашим потребам, зараз може бути гарний момент повернутися до замовлення.")}
        """
    )
    return subject, _wrap_email(subject, body)


def _payment_link_paragraph(url: str) -> str:
    safe_href = _esc(url)
    return (
        f'<p style="margin:0 0 {PARAGRAPH_GAP}px;font-family:{FONT_BODY};font-size:15px;'
        f'line-height:{LINE_BODY};color:{COLOR_TEXT};">'
        f'Посилання на оплату: '
        f'<a href="{safe_href}" target="_blank" style="color:{COLOR_PRIMARY};font-weight:600;text-decoration:underline;">'
        f'оплатити замовлення</a></p>'
    )


def render_abandoned_payment_email(
    name: str | None,
    checkout_url: str | None = None,
    products: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    subject = "Оплату не завершено"
    url = (checkout_url or "").strip() or CHECKOUT_URL_DEFAULT
    catalog = products_order_lines_html(products or [])
    body = _content_section(
        f"""
        {_greeting_line(name)}
        {_paragraph("Схоже, під час оформлення замовлення щось зупинило процес оплати.")}
        {_paragraph("Ми зберегли Ваше замовлення, тож Ви можете повернутися до його оплати без повторного оформлення.")}
        {catalog}
        {_payment_link_paragraph(url)}
        {_paragraph("Якщо виникли труднощі з оплатою або хочете уточнити деталі щодо продуктів — ми поруч і готові допомогти.")}
        {_closing_line(f'Ваш кошик чекатиме на Вас <span style="color:{COLOR_PRIMARY};">❤️</span>')}
        """
    )
    return subject, _wrap_email(subject, body)

def _btn_outline_center(label: str, href: str) -> str:
    safe_label = _esc(label)
    safe_href = _esc(href)
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:{BTN_BLOCK_MARGIN};">
      <tr>
        <td align="center" style="border-radius:999px;border:1px solid {COLOR_PRIMARY};">
          <a href="{safe_href}" target="_blank"
            style="display:inline-block;padding:14px 32px;font-family:{FONT_BODY};font-size:13px;font-weight:600;
            color:{COLOR_PRIMARY};text-decoration:none;border-radius:999px;">
            {safe_label}
          </a>
        </td>
      </tr>
    </table>"""


def _label(text: str) -> str:
    return (
        f'<p style="margin:0 0 {SPACE_MD}px;font-family:{FONT_BODY};font-size:11px;letter-spacing:0.14em;'
        f'text-transform:uppercase;color:{COLOR_PRIMARY};font-weight:600;">{_esc(text)}</p>'
    )


def _hero_row(image_url: str, *, height_style: str = "auto") -> str:
    return f"""
    <tr>
      <td style="padding:0;line-height:0;background:#F3F0EF;">
        <img src="{_esc(image_url)}" alt="" width="100%"
          style="display:block;width:100%;max-width:100%;height:{height_style};border:0;object-fit:cover;">
      </td>
    </tr>"""


def _promo_code_box(code: str, hint: str = "", *, label: str = "Промокод") -> str:
    hint_html = (
        f'<p style="margin:{SPACE_SM}px 0 0;font-family:{FONT_BODY};font-size:13px;line-height:{LINE_BODY};color:{COLOR_MUTED};">{_esc(hint)}</p>'
        if hint
        else ""
    )
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
      style="margin:{SPACE_XL}px 0;border:1px solid {COLOR_BORDER};border-radius:12px;background:{COLOR_BG};">
      <tr>
        <td align="center" class="email-box-pad" style="padding:{SPACE_LG}px 20px;">
          <p style="margin:0 0 {SPACE_XS}px;font-family:{FONT_BODY};font-size:12px;color:{COLOR_MUTED};">{_esc(label)}</p>
          <p style="margin:0;font-family:{FONT_DISPLAY};font-size:34px;letter-spacing:0.1em;color:{COLOR_PRIMARY};font-weight:600;user-select:all;-webkit-user-select:all;">{_esc(code)}</p>
          {hint_html}
        </td>
      </tr>
    </table>"""


def _format_expires_at_display(expires_at: str, lang: str = "uk") -> str:
    raw = str(expires_at or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(normalized, fmt)
            return f"{dt.day:02d}.{dt.month:02d}.{dt.year} {dt.hour:02d}:{dt.minute:02d}"
        except ValueError:
            continue
    return raw


def _parse_expires_at_aware(
    expires_at: str,
    tz_name: str = "Europe/Kyiv",
) -> datetime | None:
    """Парсить expires_at як локальний час (за замовч. Київ) → aware datetime."""
    raw = str(expires_at or "").strip()
    if not raw:
        return None
    normalized = raw.replace("T", " ")[:19]
    dt_naive: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt_naive = datetime.strptime(normalized, fmt)
            break
        except ValueError:
            continue
    if dt_naive is None:
        return None
    tz_raw = str(tz_name or "Europe/Kyiv").strip() or "Europe/Kyiv"
    if tz_raw in ("Europe/Kiev", "Europe/KYIV"):
        tz_raw = "Europe/Kyiv"
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_raw)
    except Exception:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/Kyiv")
    return dt_naive.replace(tzinfo=tz)


# Countdown GIF — власний endpoint (Stripo CDN ігнорує ?time= і рахує до кінця дня).
_PROMO_COUNTDOWN_PUBLIC_BASE = "https://cosmyfinances.telebots.site"
_PROMO_COUNTDOWN_W = 278
_PROMO_COUNTDOWN_H = 50


def _promo_countdown_gif_url(ends_ts: int) -> str:
    """Live GIF з нашого API: ends = unix timestamp кінця (сек)."""
    return f"{_PROMO_COUNTDOWN_PUBLIC_BASE}/api/mailings/countdown.gif?ends={int(ends_ts)}"


def resolve_promo_timer_ends_at(*, valid_hours: int = 24) -> datetime:
    """Кінець для countdown: момент відправки листа + valid_hours (Europe/Kyiv)."""
    hours = max(1, int(valid_hours or 24))
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Kyiv"))
    except Exception:
        from datetime import timezone

        now = datetime.now(timezone.utc)
    from datetime import timedelta

    return now + timedelta(hours=hours)


def _promo_signup_countdown_img(
    *,
    valid_hours: int = 24,
) -> str:
    dt = resolve_promo_timer_ends_at(valid_hours=valid_hours)
    src = _promo_countdown_gif_url(int(dt.timestamp()))
    return (
        f'<img class="timer" src="{_esc(src)}" alt="Залишилось часу" '
        f'width="{_PROMO_COUNTDOWN_W}" height="{_PROMO_COUNTDOWN_H}" '
        f'style="display:block;width:{_PROMO_COUNTDOWN_W}px;max-width:{_PROMO_COUNTDOWN_W}px;'
        f'height:{_PROMO_COUNTDOWN_H}px;border:0;outline:none;text-decoration:none;margin:0 auto">'
    )


def _unsubscribe_line(unsubscribe_url: str, lang: str) -> str:
    label = "Відписатися" if lang == "uk" else "Отписаться"
    return (
        f'<p style="margin:{SPACE_XXL}px 0 0;font-family:{FONT_BODY};font-size:12px;line-height:1.5;'
        f'color:{COLOR_MUTED};text-align:center;">'
        f'<a href="{_esc(unsubscribe_url)}" target="_blank" '
        f'style="color:{COLOR_MUTED};text-decoration:underline;">{_esc(label)}</a></p>'
    )


_PROMO_SIGNUP_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "email_templates_src" / "promo_signup.html"
)

_PROMO_SIGNUP_COPY: dict[str, str] = {
    "subject": "Ваш промокод −{discount}% на Cosmy",
    "email_title": "Ваш промокод −{discount}% на Cosmy",
    "top_banner": "Безкоштовна доставка від 2500 грн",
    "thanks_html": (
        "<strong>Дякуємо</strong>, що приєднались до Cosmy "
        '<strong style="font-weight:400">❤️</strong>'
    ),
    "lead_html": (
        "Ми підготували для вас персональний промокод зі знижкою "
        "<strong>–{discount}% на наступне замовлення</strong>."
    ),
    "validity_note": (
        "Промокод діє обмежений час — залишок на таймері нижче."
    ),
    "expires_until": "Дійсний до {expires}",
    "min_total": "Мінімальна сума замовлення: {amount} грн",
    "rules_note": "*Не сумується з іншими акційними пропозиціями.",
    "cta": "Перейти до магазину",
    "closing_line1": "З турботою,",
    "closing_line2": "команда COSMY ❤️",
    "nav_account": "Особистий кабінет",
    "nav_delivery": "Доставка і оплата",
    "nav_about": "Про нас",
    "contacts_phone": "Контакти: &nbsp;+38 073 317 54 43",
    "contacts_email": "email cosmy.ua@gmail.com",
}


def normalize_promo_signup_lang(raw_lang: str | None, raw_language: str | None = None) -> str:
    """Лише українська версія листа; lang з API ігнорується (сумісність)."""
    return "uk"


def _promo_signup_min_total_block(copy: dict[str, str], min_total: float) -> str:
    try:
        min_val = float(min_total or 0)
    except (TypeError, ValueError):
        min_val = 0.0
    if min_val <= 0:
        return ""
    amount = f"{min_val:,.0f}".replace(",", " ")
    text = copy["min_total"].format(amount=amount)
    return (
        "<tr><td align=\"center\" class=\"pad\" "
        "style=\"padding:4px 28px 8px;font-family:Arial,Helvetica,sans-serif;"
        f'font-size:14px;line-height:1.45;color:#333333;font-style:italic;">{_esc(text)}</td></tr>'
    )


def _promo_signup_expires_block(
    copy: dict[str, str],
    expires_display: str,
    *,
    valid_hours: int = 24,
) -> str:
    """Live countdown: скільки ще дійсний промокод."""
    return _promo_signup_countdown_img(valid_hours=valid_hours)


def render_promo_signup_email(
    *,
    code: str,
    discount_percent: int = 10,
    valid_hours: int = 24,
    expires_at: str = "",
    expires_at_tz: str = "Europe/Kyiv",
    min_total: float = 0,
    store_url: str = SITE_URL,
    unsubscribe_url: str = "",
    lang: str = "uk",
) -> tuple[str, str]:
    """Stripo-шаблон «Промо за підписку» (лише uk) + змінні з API promo-signup."""
    copy = _PROMO_SIGNUP_COPY
    discount = max(1, int(discount_percent or 10))
    hours = max(1, int(valid_hours or 24))
    expires_display = _format_expires_at_display(expires_at, "uk")
    shop_url = str(store_url or SITE_URL).strip() or SITE_URL
    tz_name = str(expires_at_tz or "Europe/Kyiv").strip() or "Europe/Kyiv"

    template = _PROMO_SIGNUP_TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "__LANG__": "uk",
        "__EMAIL_TITLE__": _esc(copy["email_title"].format(discount=discount)),
        "__TOP_BANNER__": _esc(copy["top_banner"]),
        "__THANKS_HTML__": copy["thanks_html"],
        "__LEAD_HTML__": copy["lead_html"].format(discount=discount),
        "__CODE__": _esc(str(code or "").strip()),
        "__VALIDITY_NOTE__": copy["validity_note"].format(hours=hours),
        "__EXPIRES_BLOCK__": _promo_signup_expires_block(
            copy,
            expires_display,
            valid_hours=hours,
        ),
        "__MIN_TOTAL_BLOCK__": _promo_signup_min_total_block(copy, min_total),
        "__RULES_NOTE__": _esc(copy["rules_note"]),
        "__CTA_LABEL__": _esc(copy["cta"]),
        "__STORE_URL__": _esc(shop_url),
        "__CLOSING_LINE1__": copy["closing_line1"],
        "__CLOSING_LINE2__": copy["closing_line2"],
        "__NAV_ACCOUNT__": _esc(copy["nav_account"]),
        "__NAV_DELIVERY__": _esc(copy["nav_delivery"]),
        "__NAV_ABOUT__": _esc(copy["nav_about"]),
        "__CONTACTS_PHONE__": copy["contacts_phone"],
        "__CONTACTS_EMAIL__": _esc(copy["contacts_email"]),
        "__UNSUBSCRIBE_BLOCK__": (
            _unsubscribe_line(str(unsubscribe_url or "").strip(), lang)
            if str(unsubscribe_url or "").strip()
            else ""
        ),
    }

    html_out = template
    for token, value in replacements.items():
        html_out = html_out.replace(token, value)

    subject = copy["subject"].format(discount=discount)
    return subject, html_out


def _discount_cols_html(items: list[tuple[str, str]]) -> str:
    """items: [(category, discount), ...]"""
    cells = []
    for title, discount in items:
        cells.append(
            f"""
        <td width="{100 // max(len(items), 1)}%" valign="top" style="padding:0 {SPACE_XS}px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
            style="border:1px solid {COLOR_BORDER};border-radius:12px;background:{COLOR_CARD};">
            <tr>
              <td align="center" style="padding:20px 12px;">
                <p style="margin:0 0 {SPACE_XS}px;font-family:{FONT_BODY};font-size:11px;text-transform:uppercase;color:{COLOR_MUTED};font-weight:600;">{_esc(title)}</p>
                <p style="margin:0;font-family:{FONT_DISPLAY};font-size:26px;color:{COLOR_PRIMARY};font-weight:600;">{_esc(discount)}</p>
              </td>
            </tr>
          </table>
        </td>"""
        )
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:{SPACE_XL}px 0 0;">
      <tr>{"".join(cells)}</tr>
    </table>"""


def _blog_article_html(
    image_url: str,
    date: str,
    title: str,
    excerpt: str,
    link: str = SITE_URL,
    *,
    large: bool = False,
) -> str:
    if large:
        return f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:{SPACE_XXL}px;">
          <tr><td style="line-height:0;padding-bottom:{SPACE_MD}px;">
            <img src="{_esc(image_url)}" alt="" width="520" style="display:block;width:100%;border-radius:12px;border:0;">
          </td></tr>
          <tr><td>
            <p style="margin:0 0 {SPACE_XS}px;font-family:{FONT_BODY};font-size:12px;color:{COLOR_MUTED};">{_esc(date)}</p>
            <p style="margin:0 0 {SPACE_SM}px;font-family:{FONT_DISPLAY};font-size:24px;line-height:1.3;color:{COLOR_TEXT};font-weight:600;">{_esc(title)}</p>
            <p style="margin:0 0 {SPACE_SM}px;font-family:{FONT_BODY};font-size:14px;line-height:{LINE_BODY};color:{COLOR_TEXT};">{_esc(excerpt)}</p>
            <a href="{_esc(link)}" style="font-family:{FONT_BODY};font-size:13px;color:{COLOR_PRIMARY};text-decoration:none;border-bottom:1px solid {COLOR_PRIMARY};">Читати далі</a>
          </td></tr>
        </table>"""
    return f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:{SPACE_LG}px;">
          <tr>
            <td width="112" valign="top" style="padding-right:{SPACE_MD}px;line-height:0;">
              <img src="{_esc(image_url)}" alt="" width="112" height="112"
                style="display:block;width:112px;height:112px;object-fit:cover;border-radius:12px;border:0;">
            </td>
            <td valign="top">
              <p style="margin:0 0 {SPACE_XS}px;font-family:{FONT_BODY};font-size:12px;color:{COLOR_MUTED};">{_esc(date)}</p>
              <p style="margin:0 0 {SPACE_SM}px;font-family:{FONT_DISPLAY};font-size:20px;line-height:1.35;color:{COLOR_TEXT};font-weight:600;">{_esc(title)}</p>
              <p style="margin:0;font-family:{FONT_BODY};font-size:13px;line-height:{LINE_BODY};color:{COLOR_TEXT};">{_esc(excerpt)}</p>
            </td>
          </tr>
        </table>"""


def _marketing_email(title: str, body_inner: str, *, hero_url: str | None = None) -> str:
    hero = _hero_row(hero_url) if hero_url else ""
    return _wrap_email(title, hero + _content_section(body_inner))


# --- Маркетингові шаблони для панелі розсилок ---

_SAMPLE_PRODUCTS = [
    {
        "name": "Сироватка з вітаміном C",
        "image_url": "https://images.unsplash.com/photo-1617897903246-719242758050?w=560&q=80&fit=crop",
        "price": 1290,
    },
    {
        "name": "Крем для зволоження",
        "image_url": "https://images.unsplash.com/photo-1570194065650-d99fb4bedf0a?w=560&q=80&fit=crop",
        "price": 890,
    },
]


def render_marketing_sale() -> tuple[str, str]:
    title = "Sale до −30% · Cosmy"
    body = f"""
        {_label("Сезонна акція · до 31 квітня")}
        <p style="margin:0 0 {SPACE_SM}px;font-family:{FONT_DISPLAY};font-size:48px;line-height:1;color:{COLOR_TEXT};font-weight:600;">−30%</p>
        <p style="margin:0 0 {SPACE_MD}px;font-family:{FONT_BODY};font-size:16px;line-height:{LINE_BODY};color:{COLOR_TEXT};">на обрані категорії</p>
        {_paragraph("Знижки на перевірені засоби догляду — обмежений час.")}
        {_discount_cols_html([("Сироватки", "−30%"), ("СПФ", "−25%"), ("Зволоження", "−20%")])}
        {_btn_primary("Перейти до знижок", SITE_URL)}
    """
    return title, _marketing_email(title, body, hero_url="https://images.unsplash.com/photo-1619451334792-150fd785ee74?w=1240&q=85&fit=crop")


def render_marketing_promo() -> tuple[str, str]:
    title = "Нові надходження · Cosmy"
    catalog = products_catalog_linked_html(_SAMPLE_PRODUCTS)
    body = f"""
        {_heading("Нові надходження")}
        {_paragraph("Обрані засоби, які вже доступні в Cosmy.")}
        {catalog}
        {_btn_outline_center("Переглянути всі новинки", SITE_URL)}
    """
    return title, _marketing_email(title, body)


def render_marketing_informational() -> tuple[str, str]:
    title = "Блог Cosmy"
    body = f"""
        {_blog_article_html(
            "https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=620&q=85&fit=crop",
            "25.12.2025",
            "Найкращі гелі для вмивання обличчя — ТОП від Cosmy",
            "Як обрати засіб, який очищає делікатно, не порушуючи бар'єр шкіри...",
            large=True,
        )}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td style="height:1px;background:{COLOR_BORDER};font-size:0;">&nbsp;</td></tr></table>
        {_blog_article_html(
            "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=240&q=80&fit=crop",
            "18.12.2025",
            "Ніацинамід: для кого і як",
            "Один із найуніверсальніших активів для щоденного догляду...",
        )}
        {_btn_outline_center("Переглянути всі поради", f"{SITE_URL}/blog" if False else SITE_URL)}
    """
    return title, _marketing_email(title, body)


def render_marketing_post_order() -> tuple[str, str]:
    title = "Як вам наш продукт? · Cosmy"
    stars = "".join(
        f'<td style="padding:0 4px;"><a href="{SITE_URL}" style="display:inline-block;width:40px;height:40px;'
        f'line-height:40px;text-align:center;border:1px solid {COLOR_BORDER};border-radius:8px;'
        f'color:{COLOR_PRIMARY};text-decoration:none;font-size:16px;">★</a></td>'
        for _ in range(5)
    )
    body = f"""
        {_label("Піклуємось про вас")}
        {_heading("Як почуваєтесь після замовлення?")}
        {_paragraph(
            "Минуло декілька днів з моменту вашого замовлення. Ми хотіли б дізнатись — "
            "все пройшло гладко? Продукти вас порадували?"
        )}
        {_paragraph("Ваш відгук допомагає нам ставати кращими — і він займе лише хвилину.")}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
          style="margin:{SPACE_XL}px 0;border:1px solid {COLOR_BORDER};border-radius:12px;">
          <tr><td align="center" style="padding:{SPACE_XL}px 20px;">
            <p style="margin:0 0 {SPACE_LG}px;font-family:{FONT_BODY};font-size:16px;line-height:{LINE_BODY};font-weight:600;color:{COLOR_TEXT};">Оцініть ваш досвід з Cosmy</p>
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:0 auto {SPACE_LG}px;"><tr>{stars}</tr></table>
            {_btn_primary("Залишити відгук", SITE_URL)}
          </td></tr>
        </table>
        {_label("Є питання?")}
        {_paragraph("Наші косметологи допоможуть з вибором догляду або відповідять на запитання щодо продуктів.")}
        {_closing_line(f'З турботою,<br><span style="font-family:{FONT_DISPLAY};font-size:20px;font-weight:600;">Команда Cosmy</span>')}
    """
    return title, _marketing_email(
        title,
        body,
        hero_url="https://images.unsplash.com/photo-1522338242992-e1a54906a8da?w=1240&q=85&fit=crop",
    )


def render_marketing_black_friday() -> tuple[str, str]:
    title = "Black Friday · Cosmy"
    body = f"""
        {_label("Black Friday · 48 годин")}
        <p style="margin:0 0 {SPACE_MD}px;font-family:{FONT_DISPLAY};font-size:52px;line-height:0.95;color:{COLOR_TEXT};font-weight:600;">до −50%</p>
        {_paragraph("На топові бренди та бестселери догляду.")}
        {_promo_code_box("BF2026", "Діє до 23:59 · Не сумується з іншими акціями")}
        {_btn_primary("Забрати знижку", SITE_URL)}
    """
    return title, _marketing_email(title, body)


def render_marketing_birthday() -> tuple[str, str]:
    title = "Подарунок до Дня народження · Cosmy"
    body = f"""
        {_label("З Днем народження")}
        {_heading("Ваш персональний подарунок")}
        {_paragraph("Даруємо спеціальну знижку на улюблені засоби догляду. Дякуємо, що Ви з Cosmy.")}
        {_promo_code_box("BIRTHDAY15", "−15% на замовлення")}
        {_btn_primary("Активувати знижку", SITE_URL)}
    """
    return title, _marketing_email(title, body)


def render_marketing_new_collection() -> tuple[str, str]:
    title = "Нова колекція догляду · Cosmy"
    body = f"""
        {_label("Нова колекція вже на сайті")}
        {_heading("Оновіть свій догляд цієї весни")}
        {_paragraph(
            "Ми додали новинки для зволоження, відновлення та щоденного захисту шкіри. "
            "Обирайте засоби під свій тип і потреби."
        )}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:{SPACE_XL}px 0 0;">
          <tr>
            <td width="50%" valign="top" style="padding:0 {SPACE_XS}px 0 0;">
              <table role="presentation" width="100%" style="border:1px solid {COLOR_BORDER};border-radius:12px;">
                <tr><td style="padding:20px;text-align:center;">
                  <p style="margin:0 0 {SPACE_XS}px;font-family:{FONT_BODY};font-size:11px;text-transform:uppercase;color:{COLOR_PRIMARY};font-weight:600;">Зволоження</p>
                  <p style="margin:0;font-family:{FONT_BODY};font-size:13px;color:{COLOR_TEXT};">Сироватки та креми</p>
                </td></tr>
              </table>
            </td>
            <td width="50%" valign="top" style="padding:0 0 0 {SPACE_XS}px;">
              <table role="presentation" width="100%" style="border:1px solid {COLOR_BORDER};border-radius:12px;">
                <tr><td style="padding:20px;text-align:center;">
                  <p style="margin:0 0 {SPACE_XS}px;font-family:{FONT_BODY};font-size:11px;text-transform:uppercase;color:{COLOR_PRIMARY};font-weight:600;">SPF</p>
                  <p style="margin:0;font-family:{FONT_BODY};font-size:13px;color:{COLOR_TEXT};">Щоденний захист</p>
                </td></tr>
              </table>
            </td>
          </tr>
        </table>
        {_btn_primary("Переглянути новинки", SITE_URL)}
    """
    return title, _marketing_email(title, body)


def all_email_templates() -> dict[str, tuple[str, str]]:
    """filename -> (subject, html)"""
    return {
        "welcome_email.html": render_welcome_email("Олена"),
        "trigger_order_placed.html": render_order_placed_email(
            "Марія",
            [
                {
                    "name": "Dr. Ceuracle Vegan Kombucha Tea Essence — есенція з комбучею, 150 мл",
                    "price": 1450,
                    "price_old": 1690,
                    "quantity": 1,
                    "image_url": _SAMPLE_PRODUCTS[0]["image_url"],
                    "product_url": SITE_URL,
                },
                {
                    "name": "Medik8 Crystal Retinal 6 Serum — сироватка з ретиналем 0.06%, 30 мл",
                    "price": 2490,
                    "quantity": 1,
                    "image_url": _SAMPLE_PRODUCTS[1]["image_url"],
                    "product_url": SITE_URL,
                },
            ],
            {
                "order_number": "В12345",
                "order_total": 3690,
                "discount": 250,
                "customer_full_name": "Потапенко Марія",
                "phone": "+380935556677",
                "payment_method": "Онлайн оплата",
                "delivery_address": "НП 5, м. Київ",
                "delivery_cost": "Безкоштовна доставка",
            },
        ),
        "trigger_abandoned_cart.html": render_abandoned_cart_email(
            "Олена",
            [
                {
                    "name": _SAMPLE_PRODUCTS[0]["name"],
                    "price": 1290,
                    "quantity": 1,
                    "image_url": _SAMPLE_PRODUCTS[0]["image_url"],
                    "product_url": SITE_URL,
                },
                {
                    "name": _SAMPLE_PRODUCTS[1]["name"],
                    "price": 890,
                    "quantity": 1,
                    "image_url": _SAMPLE_PRODUCTS[1]["image_url"],
                    "product_url": SITE_URL,
                },
            ],
        ),
        "trigger_abandoned_payment.html": render_abandoned_payment_email(
            "Олена",
            products=[
                {
                    "name": _SAMPLE_PRODUCTS[0]["name"],
                    "price": 1290,
                    "quantity": 1,
                    "image_url": _SAMPLE_PRODUCTS[0]["image_url"],
                    "product_url": SITE_URL,
                },
            ],
        ),
        "trigger_price_drop_wishlist.html": render_price_drop_wishlist_email(
            "Олена",
            [
                {
                    "name": _SAMPLE_PRODUCTS[0]["name"],
                    "image_url": _SAMPLE_PRODUCTS[0]["image_url"],
                    "old_price": 1490,
                    "new_price": 1290,
                    "product_url": SITE_URL,
                }
            ],
        ),
        "trigger_price_drop_cart.html": render_price_drop_cart_email(
            "Олена",
            [
                {
                    "name": _SAMPLE_PRODUCTS[0]["name"],
                    "image_url": _SAMPLE_PRODUCTS[0]["image_url"],
                    "old_price": 990,
                    "new_price": 790,
                    "product_url": SITE_URL,
                },
                {
                    "name": _SAMPLE_PRODUCTS[1]["name"],
                    "image_url": _SAMPLE_PRODUCTS[1]["image_url"],
                    "old_price": 1100,
                    "new_price": 890,
                    "product_url": SITE_URL,
                },
            ],
        ),
        "trigger_promo_signup.html": render_promo_signup_email(
            code="CMK7QW2P",
            discount_percent=10,
            valid_hours=24,
            expires_at="2026-08-01 15:04:00",
            store_url=SITE_URL,
            unsubscribe_url=f"{SITE_URL}/index.php?route=promo/unsubscribe&t=sample",
        ),
        "sale.html": render_marketing_sale(),
        "promo.html": render_marketing_promo(),
        "informational.html": render_marketing_informational(),
        "post_order.html": render_marketing_post_order(),
        "black_friday.html": render_marketing_black_friday(),
        "birthday_offer.html": render_marketing_birthday(),
        "new_collection.html": render_marketing_new_collection(),
    }


def write_all_email_templates(output_dir: str | None = None) -> list[str]:
    from pathlib import Path

    out = Path(output_dir or Path(__file__).resolve().parent.parent / "email_templates")
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, (subject, html) in all_email_templates().items():
        (out / name).write_text(html, encoding="utf-8")
        written.append(name)
    return written
