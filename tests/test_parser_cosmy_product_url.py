from web.parser_service import (
    _catalog_item_product_url,
    _cosmy_product_url,
    _infer_product_option_value_id,
)


def test_cosmy_url_with_option_value_id():
    url = _cosmy_product_url(5210, product_option_value_id=2712)
    assert url is not None
    assert "product_id=5210" in url
    assert "product_option_value_id=2712" in url


def test_infer_pov_from_model_suffix():
    assert _infer_product_option_value_id("04906-2712") == 2712
    assert _infer_product_option_value_id("105518") is None


def test_catalog_item_url_from_model():
    url = _catalog_item_product_url(
        {"product_id": 5210, "model": "04906-2711", "option_value": "1000ml"},
    )
    assert "product_option_value_id=2711" in (url or "")
