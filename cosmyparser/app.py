#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Веб-інтерфейс порівняння цін конкурентів."""

from __future__ import annotations

import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from catalog_report import REPORT_XLSX, load_catalog, run_catalog  # noqa: E402
from cosmy_parse import SITES, parse_price, search_all  # noqa: E402
from web.parser_store import load_history, load_report, save_history  # noqa: E402

DATA_DIR = ROOT.parent / "databases" / "parser"
MAX_HISTORY = 40

app = Flask(__name__)
REPORT_STATUS = {"state": "idle"}
REPORT_LOCK = threading.Lock()


def history_preview(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "query": item.get("query"),
        "our_price": item.get("our_price"),
        "created_at": item.get("created_at"),
        "found": item.get("found"),
        "market_avg": item.get("market_avg"),
        "market_min": item.get("market_min"),
        "market_max": item.get("market_max"),
        "position": item.get("position"),
    }


@app.get("/")
def index():
    return render_template(
        "index.html",
        sites=[site.name for site in SITES],
        catalog=load_catalog(),
    )


@app.get("/api/sites")
def api_sites():
    return jsonify({"sites": [site.name for site in SITES]})


@app.get("/api/history")
def api_history():
    items = load_history()
    return jsonify({"items": [history_preview(item) for item in items]})


@app.get("/api/history/<item_id>")
def api_history_item(item_id: str):
    for item in load_history():
        if item.get("id") == item_id:
            return jsonify(item)
    return jsonify({"error": "Запис не знайдено"}), 404


@app.delete("/api/history/<item_id>")
def api_history_delete(item_id: str):
    items = [item for item in load_history() if item.get("id") != item_id]
    save_history(items)
    return jsonify({"ok": True, "items": [history_preview(item) for item in items]})


@app.delete("/api/history")
def api_history_clear():
    save_history([])
    return jsonify({"ok": True, "items": []})


@app.post("/api/search")
def api_search():
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or request.form.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Введіть назву товару"}), 400

    our_price = parse_price(payload.get("our_price") or request.form.get("our_price"))
    result = search_all(query, our_price=our_price, debug=False)
    record = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    history = load_history()
    history.insert(0, record)
    save_history(history)
    record["history"] = [history_preview(item) for item in history]
    return jsonify(record)


@app.get("/api/catalog")
def api_catalog():
    return jsonify({"items": load_catalog()})


@app.get("/api/report/status")
def api_report_status():
    payload = dict(REPORT_STATUS)
    payload["has_file"] = REPORT_XLSX.exists()
    if payload.get("state") != "running":
        data = load_report("full") or {}
        payload.setdefault("generated_at", data.get("generated_at"))
        payload.setdefault("total", data.get("total"))
    return jsonify(payload)


def _run_report():
    global REPORT_STATUS

    def progress(status):
        global REPORT_STATUS
        REPORT_STATUS = status

    try:
        run_catalog(progress=progress)
    except Exception as exc:
        REPORT_STATUS = {"state": "error", "error": str(exc)}


@app.post("/api/report/run")
def api_report_run():
    global REPORT_STATUS
    with REPORT_LOCK:
        if REPORT_STATUS.get("state") == "running":
            return jsonify(REPORT_STATUS), 409
        REPORT_STATUS = {"state": "running", "current": 0, "total": len(load_catalog())}
        threading.Thread(target=_run_report, daemon=True).start()
    return jsonify(REPORT_STATUS)


@app.get("/api/report/download")
def api_report_download():
    if not REPORT_XLSX.exists():
        return jsonify({"error": "Звіт ще не згенеровано"}), 404
    return send_file(
        REPORT_XLSX,
        as_attachment=True,
        download_name="cosmy-price-report.xlsx",
    )


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=5050, debug=True, threaded=True)
