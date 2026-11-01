#!/usr/bin/env python3
"""Перегенерувати всі HTML-шаблони листів у email_templates/ з web/cosmy_email.py."""

from web.cosmy_email import write_all_email_templates

if __name__ == "__main__":
    files = write_all_email_templates()
    print(f"Оновлено {len(files)} шаблонів:")
    for name in files:
        print(f"  - {name}")
