import requests, re
from bs4 import BeautifulSoup
H={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"uk-UA,uk;q=0.9"}
r=requests.get("https://kasta.ua/uk/search/?q=sesderma",headers=H,timeout=20)
soup=BeautifulSoup(r.content,"lxml")
for c in soup.select("div.p__content")[:8]:
    spans=[(s.get("class"), s.get_text(" ",strip=True)) for s in c.select(".p__info_container .expand *") if "₴" in s.get_text()]
    name=c.select_one(".p__info_name")
    print(name.get_text(strip=True)[:70] if name else None)
    for cl,t in spans[:4]: print("    ",cl,t[:30])
print("pages:", sorted(set(re.findall(r'href="([^"]*search[^"]*page=\d+[^"]*)"', r.text)))[:5])
for p in (2,3):
    r2=requests.get(f"https://kasta.ua/uk/search/?q=sesderma&page={p}",headers=H,timeout=20)
    s2=BeautifulSoup(r2.content,"lxml")
    print("kasta page",p,len(s2.select("div.p__content")), [x.get_text(strip=True)[:40] for x in s2.select(".p__info_name")[:2]])
for p in (1,2,3,4):
    r3=requests.get(f"https://makeup.com.ua/ua/search/?q=Sesderma&page={p}",headers=H,timeout=20)
    s3=BeautifulSoup(r3.content,"lxml")
    tiles=s3.select("[data-testid='product-tile']")
    ses=[t for t in tiles if "sesderma" in t.get_text(" ",strip=True).lower()]
    print("makeup page",p,"tiles",len(tiles),"ses",len(ses), [t.select_one("a[href*='/product/']")["href"] for t in ses[:2]])
