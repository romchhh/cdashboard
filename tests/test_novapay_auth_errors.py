from novapay.auth import format_novapay_user_message


def test_forbidden_error_includes_hint():
    msg = format_novapay_user_message("Principal authorization is forbidden!")
    assert "Principal authorization is forbidden!" in msg
    assert "UserAuthenticationJWT" in msg
    assert "business.novapay.ua" in msg


def test_unknown_error_unchanged():
    assert format_novapay_user_message("Something else") == "Something else"
