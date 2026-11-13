"""Залишки: фід vs логіка збереження (варіації Needly та подібні)."""

from xml.etree import ElementTree as ET

from bot.services.feed_stock import (
    find_feed_products_by_query,
    parse_feed_product_stock,
)


def _product_xml(inner: str) -> ET.Element:
    return ET.fromstring(f"<product>{inner}</product>")


def test_simple_product_stock_qty_on_product_level():
    el = _product_xml(
        """
        <product_id>1001</product_id>
        <name>Крем без опцій</name>
        <has_options>0</has_options>
        <stock_qty>2</stock_qty>
        """
    )
    snap = parse_feed_product_stock(el)
    assert snap is not None
    assert snap.product_stock_qty == 2
    assert snap.feed_effective_stock == 2
    assert not snap.options


def test_needly_style_stock_on_option_product_stock_zero():
    """Типовий кейс: у фіді product stock_qty=0, на опції stock_qty=2."""
    el = _product_xml(
        """
        <product_id>2002</product_id>
        <name>Восстанавливающий барьерный крем для сухой и чувствительной кожи - Needly A to Cream</name>
        <brand>Needly</brand>
        <has_options>1</has_options>
        <stock_qty>0</stock_qty>
        <options>
          <option>
            <product_option_value_id>55</product_option_value_id>
            <option_value_name>50 ml</option_value_name>
            <stock_qty>2</stock_qty>
          </option>
        </options>
        """
    )
    snap = parse_feed_product_stock(el)
    assert snap is not None
    assert snap.has_options is True
    assert snap.product_stock_qty == 0
    assert snap.options_stock_sum == 2
    assert snap.feed_effective_stock == 2
    hint = snap.describe_mismatch_hint(db_product_stock=0, db_options_sum=2)
    assert "product.stock_qty" not in hint or "варіації" in hint or hint == ""


def test_mismatch_when_db_lost_option_stock():
    el = _product_xml(
        """
        <product_id>3003</product_id>
        <name>Needly A to Cream</name>
        <has_options>1</has_options>
        <stock_qty>0</stock_qty>
        <options>
          <option>
            <stock_qty>2</stock_qty>
          </option>
        </options>
        """
    )
    snap = parse_feed_product_stock(el)
    assert snap is not None
    hint = snap.describe_mismatch_hint(db_product_stock=0, db_options_sum=0)
    assert "сума option.stock_qty" in hint
    assert "фід=2" in hint
    assert "БД=0" in hint


def test_find_by_needly_name_in_temp_feed(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<analytics>
  <categories><category id="1"><name>X</name></category></categories>
  <brands><brand id="1"><name>Needly</name></brand></brands>
  <products>
    <product>
      <product_id>9001</product_id>
      <name>Восстанавливающий барьерный крем для сухой и чувствительной кожи - Needly A to Cream</name>
      <brand>Needly</brand>
      <has_options>1</has_options>
      <stock_qty>0</stock_qty>
      <options><option><stock_qty>2</stock_qty></option></options>
    </product>
  </products>
</analytics>
""",
        encoding="utf-8",
    )
    hits = find_feed_products_by_query(str(feed), "Needly A to Cream")
    assert len(hits) == 1
    assert hits[0].product_id == 9001
    assert hits[0].feed_effective_stock == 2
