"""Тести відписки від email-розсилок."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from web.mailing_unsubscribe import (
    filter_unsubscribed_emails,
    is_marketing_trigger,
    make_unsubscribe_token,
    parse_unsubscribe_token,
    unsubscribe_url_for_email,
)


class MailingUnsubscribeTests(unittest.TestCase):
    def test_token_roundtrip(self):
        token = make_unsubscribe_token("Shopper@Example.com")
        self.assertEqual(parse_unsubscribe_token(token), "shopper@example.com")

    def test_unsubscribe_url_uses_public_base(self):
        with patch("web.mailing_unsubscribe.WEB_PUBLIC_BASE_URL", "https://fin.example.com"):
            with patch(
                "web.mailing_unsubscribe.EMAIL_UNSUBSCRIBE_URL",
                "{base}/index.php?route=promo/unsubscribe&t={token}",
            ):
                url = unsubscribe_url_for_email("a@b.com")
        self.assertIn("https://fin.example.com/index.php?route=promo/unsubscribe&t=", url)
        self.assertEqual(parse_unsubscribe_token(url.split("t=", 1)[1]), "a@b.com")

    def test_is_marketing_trigger(self):
        self.assertTrue(is_marketing_trigger("promo_signup"))
        self.assertTrue(is_marketing_trigger("manual"))
        self.assertFalse(is_marketing_trigger("order"))
        self.assertFalse(is_marketing_trigger("welcome"))

    def test_filter_unsubscribed_emails(self):
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = ["b@example.com"]
        session.execute = AsyncMock(return_value=result)

        kept, skipped = asyncio.run(
            filter_unsubscribed_emails(
                session,
                ["a@example.com", "b@example.com", "c@example.com"],
            )
        )
        self.assertEqual(kept, ["a@example.com", "c@example.com"])
        self.assertEqual(skipped, 1)

    def test_filter_large_list_loads_all_unsubscribes(self):
        """Велика база email не повинна йти в один SQLite IN (...) — лише SELECT відписок."""
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = ["blocked@example.com"]
        session.execute = AsyncMock(return_value=result)

        emails = [f"user{i}@example.com" for i in range(1200)] + ["blocked@example.com"]
        kept, skipped = asyncio.run(filter_unsubscribed_emails(session, emails))

        self.assertEqual(skipped, 1)
        self.assertNotIn("blocked@example.com", kept)
        self.assertEqual(len(kept), 1200)
        self.assertEqual(session.execute.await_count, 1)
        sql = str(session.execute.await_args.args[0])
        self.assertNotIn("IN (", sql.upper().replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
