from bot.services.classifier import forced_expense_category


def test_black_card_transfer_is_visible_expense():
    assert forced_expense_category("На чорну картку", "Беднарчук Дарія") == "Переказ на картку"


def test_white_card_transfer_is_visible_expense():
    assert forced_expense_category("На білу картку") == "Переказ на картку"


def test_card_purchase_is_visible_expense():
    purpose = "Купівля в торгівельно-сервісній мережі 513435******5902, ФОП Беднарчук"
    assert forced_expense_category(purpose) == "Оплата карткою"


def test_customer_payout_is_not_a_card_debit():
    assert forced_expense_category("Переказ коштів по платежам, прийнятим від населення") is None
