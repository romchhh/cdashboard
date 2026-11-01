import requests, re, sys
from bs4 import BeautifulSoup
from collections import Counter
H={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"uk-UA,uk;q=0.9"}
urls=sys.argv[1:]
for url in urls:
    try:
        r=requests.get(url,headers=H,timeout=20)
    except Exception as e:
        print(url,"ERR",e); continue
    soup=BeautifulSoup(r.text,"lxml")
    print(f"\n## {r.status_code} {r.url[:100]} len={len(r.text)} ses={r.text.lower().count('sesderma')}")
    anchors=[a for a in soup.find_all("a",href=True) if "sesderma" in a.get_text(" ",strip=True).lower() and len(a.get_text(strip=True))>15]
    chains=Counter()
    for a in anchors:
        p=a.parent; chain=[]
        for _ in range(3):
            if p is None: break
            chain.append(p.name+"."+".".join((p.get("class") or [])[:2])); p=p.parent
        chains[" < ".join(chain)]+=1
    print("  product-like anchors:", len(anchors))
    for c,n in chains.most_common(2): print("   ",n,c)
    for a in anchors[:2]:
        print("    -", a["href"][:100], "|", a.get_text(" ",strip=True)[:70])
    pages=sorted(set(re.findall(r'href="([^"]*(?:page[=/-]|/page/|PAGEN_\d+=)\d+[^"]*)"', r.text)))[:6]
    print("  pagination:", pages)
