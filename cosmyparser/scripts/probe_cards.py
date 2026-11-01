import requests, sys
from urllib.parse import quote
from bs4 import BeautifulSoup
from collections import Counter
H={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"uk-UA,uk;q=0.9"}
q=quote("Sesderma C-Vit")
urls={
 "kosmetolog":f"https://kosmetolog.ua/ua/kataloh/search/?q={q}",
 "laserhouse":f"https://www.laserhouse.com.ua/ua/search/?q={q}",
 "kstore":f"https://kstore.com.ua/search/?q={q}",
 "kstore2":f"https://kstore.com.ua/?s={q}&post_type=product",
 "beautis":f"https://beautis.com.ua/ua/search/?search={q}",
 "pureshop":f"https://pureshop.com.ua/search/?searchword={q}",
}
for name,url in urls.items():
    try:
        r=requests.get(url,headers=H,timeout=20)
    except Exception as e:
        print(name,"ERR",e); continue
    soup=BeautifulSoup(r.text,"lxml")
    print(f"\n## {name} {r.status_code} {r.url[:100]} len={len(r.text)} ses={r.text.lower().count('sesderma')}")
    # find anchors whose text contains sesderma and look at ancestor class chains
    anchors=[a for a in soup.find_all("a",href=True) if "sesderma" in a.get_text(" ",strip=True).lower()]
    chains=Counter()
    for a in anchors:
        p=a.parent; chain=[]
        for _ in range(4):
            if p is None: break
            chain.append(p.name+"."+".".join((p.get("class") or [])[:2]))
            p=p.parent
        chains[" < ".join(chain)]+=1
    print("  anchors with sesderma:", len(anchors))
    for c,n in chains.most_common(3): print("   ",n,c)
    for a in anchors[:4]:
        print("    -", a["href"][:90], "|", a.get_text(" ",strip=True)[:70], "| a.class=", a.get("class"))
