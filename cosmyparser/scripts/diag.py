import sys, os
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "cosmyparser"))
import time
from concurrent.futures import ThreadPoolExecutor
from cosmy_parse import SITES, search_one_site
from web.parser_store import load_catalog_products

cat = load_catalog_products()
ids = [int(x) for x in sys.argv[1].split(",")]
items = [p for p in cat if p["id"] in ids]
samples = tuple(p["query"] for p in cat[:3])


def run(site, p):
    t = time.time()
    try:
        r = search_one_site(site, p["query"], our_price=p.get("our_price"), samples=samples)
    except Exception as e:
        r = {"status": "error", "reason": repr(e)}
    r["_t"] = round(time.time() - t, 1)
    return site.name, r


for p in items:
    print(f"\n=== #{p['id']} {p['query']} (наша {p['our_price']})")
    with ThreadPoolExecutor(len(SITES)) as ex:
        res = list(ex.map(lambda s: run(s, p), SITES))
    for name, r in res:
        if r.get("status") == "ok":
            stock = "" if r.get("in_stock", True) else " [нема]"
            print(f"  OK  {name:13} {r.get('price'):>7} m={r.get('match',0):.2f} {r.get('volume') or '':7} "
                  f"{r.get('volume_match'):8}{stock} {r.get('_t')}s  {(r.get('title') or '')[:70]}")
        elif r.get("status") != "no_brand":
            bt = r.get("best_title") or ""
            print(f"  --  {name:13} {r.get('status'):9} {r.get('_t')}s {r.get('reason','')[:45]} | {bt[:60]} {r.get('match') or ''}")
    nb = [name for name, r in res if r.get("status") == "no_brand"]
    print(f"  без бренду: {', '.join(nb)}")
