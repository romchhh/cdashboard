import requests
from bs4 import BeautifulSoup
H={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"uk-UA,uk;q=0.9"}
def dump(url, card, psel):
    s=BeautifulSoup(requests.get(url,headers=H,timeout=20).content,"lxml")
    seen=0
    for c in s.select(card):
        p=c.select_one(psel)
        if p is None: continue
        html=str(p)
        if seen<2 or ("old" in html or "strike" in html or "del" in html):
            print(url.split('/')[2], "|", html[:400].replace("\n"," "))
            seen+=1
        if seen>=4: break
dump("https://kstore.com.ua/shop/brendi/sesderma/","div.product-block","[class*=price]")
dump("https://kstore.com.ua/shop/brendi/sesderma/page-2/","div.product-block","[class*=price]")
dump("https://kosmetolog.ua/ua/sesderma/","div.catalogCard",".catalogCard-price")
dump("https://kosmetolog.ua/ua/sesderma/filter/page=2/","div.catalogCard",".catalogCard-price")
dump("https://beautis.com.ua/ua/brand/sesderma/","div.product-slider-item",".prices")
dump("https://beautis.com.ua/ua/brand/sesderma/?page=2","div.product-slider-item",".prices")
