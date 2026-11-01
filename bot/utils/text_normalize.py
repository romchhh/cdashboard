import html
import re
from typing import Any


_BROKEN_APOS_ENTITY_RE = re.compile(r"&[аa][рp][оo];", flags=re.IGNORECASE)


def normalize_product_text(value: Any) -> Any:
    """
    Нормалізує рядки, які приходять з фіда/БД у вигляді HTML-ентіті.
    Основна ціль: прибрати '&apos;' (включно з подвійним екрануванням '&amp;apos;')
    та зламані варіанти типу '&аро;'.

    Для не-рядків повертає значення без змін.
    """
    if not isinstance(value, str):
        return value

    s = value.strip()
    if not s:
        return value

    # Фід/дані інколи містять подвійне екранування: "&amp;apos;".
    # Двічі unescape покриває більшість випадків.
    for _ in range(2):
        s = html.unescape(s)

    # Космі фід інколи віддає "&аро;" (суміш кирилиці/латиниці) замість апострофа.
    s = _BROKEN_APOS_ENTITY_RE.sub("’", s)

    return s

