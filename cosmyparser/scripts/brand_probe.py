import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from concurrent.futures import ThreadPoolExecutor
from cosmy_parse import SITES, find_search_results
brand = sys.argv[1]
qs = sys.argv[2:] or [brand]
def run(site):
    out = []
    for q in qs:
        try:
            items = find_search_results(site, q)
        except Exception as e:
            items = []
        hits = [i for i in items if brand.lower() in ((i.get("title") or "") + i.get("url","")).lower()]
        out.append((q, len(items), len(hits), [ (h.get("title") or h.get("url"))[:70] for h in hits[:3]]))
    return site.name, out
with ThreadPoolExecutor(13) as ex:
    for name, out in ex.map(run, SITES):
        for q, n, h, sample in out:
            print(f"{name:13} q={q[:35]:35} results={n:3} brand_hits={h:3} {sample}")
