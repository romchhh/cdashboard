import requests, re
from bs4 import BeautifulSoup
H={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"uk-UA,uk;q=0.9"}
def show(url, card, fields):
    r=requests.get(url,headers=H,timeout=20); soup=BeautifulSoup(r.content,"lxml")
    cards=soup.select(card); print(f"\n## {url} cards={len(cards)}")
    for c in cards[:3]:
        out={}
        for k,s in fields.items():
            el=c.select_one(s)
            if el is None: out[k]=None; continue
            out[k]= el.get("href") or el.get("data-src") or el.get("src") if k in("url","img") else el.get_text(" ",strip=True)[:80]
        print("  ",out)
show("https://kstore.com.ua/shop/brendi/sesderma/","div.product-block",
     {"title":".product-name","price":".product-price, .price, [class*=price]","url":".product-name a","img":"img.product-image, .product-image img, img[data-src*='Products'], img"})
show("https://kosmetolog.ua/ua/sesderma/","div.catalogCard",
     {"title":".catalogCard-title","price":".catalogCard-price, [class*=price]","url":".catalogCard-title a","img":"img.catalogCard-img"})
show("https://kstore.com.ua/shop/brendi/sesderma/page-3/","div.product-block",{"title":".product-name"})
show("https://kosmetolog.ua/ua/sesderma/filter/page=7/","div.catalogCard",{"title":".catalogCard-title"})
show("https://kosmetolog.ua/ua/sesderma/filter/page=30/","div.catalogCard",{"title":".catalogCard-title"})
show("https://beautis.com.ua/ua/brand/sesderma/?page=6","div.product-slider-item",{"title":".product-name"})
show("https://beautis.com.ua/ua/brand/sesderma/?page=40","div.product-slider-item",{"title":".product-name"})
