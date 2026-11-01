import requests, re
from bs4 import BeautifulSoup
H={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"uk-UA,uk;q=0.9"}
for base in ["https://kosmetolog.ua/ua/","https://pureshop.com.ua/","https://www.laserhouse.com.ua/ua/","https://kstore.com.ua/","https://beautis.com.ua/ua/"]:
    try:
        r=requests.get(base,headers=H,timeout=15)
    except Exception as e:
        print(base,"ERR",e); continue
    soup=BeautifulSoup(r.text,"lxml")
    print("\n##", base, r.status_code)
    for f in soup.find_all("form"):
        inputs=[(i.get("name"),i.get("type")) for i in f.find_all("input")]
        if any(n and n.lower() in ("q","s","search","query","text","keyword","filter_name","search_query") for n,_ in inputs):
            print("  form action=",f.get("action"),"method=",f.get("method"),"inputs=",inputs[:6])
    m=re.findall(r'["\'](/[^"\']*search[^"\']*)["\']', r.text)
    print("  search-ish paths:", sorted(set(m))[:10])
