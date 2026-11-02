"""Фід продуктів: дочірні поля мають бути на місці, коли читаємо товар."""

from bot.services.products_sync import _iter_feed_nodes, _text, feed_contains_products


_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<analytics>
  <categories>
    <category id="5" parent_id="0">
      <name>Догляд</name>
    </category>
  </categories>
  <brands>
    <brand id="9">
      <name>Academie</name>
    </brand>
  </brands>
  <products>
    <product>
      <product_id>42</product_id>
      <name>Крем</name>
      <brand_id>9</brand_id>
      <options>
        <option>
          <product_option_value_id>7</product_option_value_id>
          <stock_qty>3</stock_qty>
        </option>
      </options>
    </product>
  </products>
</analytics>
"""


def test_feed_keeps_product_children(tmp_path):
    path = tmp_path / "feed.xml"
    path.write_text(_FEED, encoding="utf-8")

    seen = []
    for tag, elem in _iter_feed_nodes(path):
        if tag == "category":
            seen.append(("category", elem.attrib.get("id"), _text(elem.find("name"))))
        elif tag == "brand":
            seen.append(("brand", elem.attrib.get("id"), _text(elem.find("name"))))
        elif tag == "product":
            option = elem.find("options/option")
            seen.append(
                (
                    "product",
                    _text(elem.find("product_id")),
                    _text(elem.find("name")),
                    _text(option.find("stock_qty")) if option is not None else "",
                )
            )

    assert seen == [
        ("category", "5", "Догляд"),
        ("brand", "9", "Academie"),
        ("product", "42", "Крем", "3"),
    ]
    assert feed_contains_products(path) is True


def test_empty_feed_does_not_count_as_products(tmp_path):
    path = tmp_path / "empty.xml"
    path.write_text("<analytics><products></products></analytics>", encoding="utf-8")
    assert feed_contains_products(path) is False
