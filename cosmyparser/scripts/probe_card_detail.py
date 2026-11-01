import requests, sys
from bs4 import BeautifulSoup
H={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"uk-UA,uk;q=0.9"}
cfg = {
 "kstore": ("https://kstore.com.ua/shop/brendi/sesderma/", "div.product-block"),
 "beautis": ("https://beautis.com.ua/ua/brand/sesderma/", "div.product-slider-item"),
 "kosmetolog": ("https://kosmetolog.ua/ua/sesderma/", "div.catalogCard"),
 "kasta": ("https://kasta.ua/uk/search/?q=sesderma", "div.p__content"),
}
for name,(url,sel) in cfg.items():
    r=requests.get(url,headers=H,timeout=20)
    soup=BeautifulSoup(r.content,"lxml")
    cards=soup.select(sel)
    print(f"\n## {name} cards={len(cards)}")
    for c in cards[:1]:
        html=str(c)
        print(html[:1800])
