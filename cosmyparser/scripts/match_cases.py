"""Регресійні пари «запит → назва в магазині → чи це той самий товар».

    .venv/bin/python scripts/match_cases.py
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cosmy_parse import candidate_score, extract_specs, finalize_product

THRESHOLD = 0.58
CASES = [
    # (запит, назва конкурента, той самий товар?)
    ("SeSDerma C-Vit Moisturizing Face Cream", "Зволожувальний крем проти зморщок SesDerma Laboratories C-Vit Moisturizing Face Cream", True),
    ("SeSDerma C-Vit Moisturizing Face Cream", "Крем для контуру очей SesDerma Laboratories C Vit Eye Contour Cream", False),
    ("SeSDerma C-Vit Moisturizing Face Cream", "Сяйний флюїд SesDerma Laboratories C-Vit Radiance Glowing Fluid", False),
    ("SeSDerma C-Vit Eye Contour Cream", "Крем навколо очей Sesderma C-VIT Ceramic Eye Contour Cream", True),
    ("SeSDerma C-Vit Eye Contour Cream", "Патчи для контура вокруг глаз Sesderma C-VIT Eye Contour Patches", False),
    ("SeSDerma Azelac Face Scalp and Body Lotion", "Лосьйон для обличчя й тіла SesDerma Laboratories Azelac Lotion", True),
    ("SeSDerma Azelac Face Scalp and Body Lotion", "Лосьйон для обличчя та тіла Sesderma Azelac Facial Body Hair Lotion 100 мл", True),
    ("SeSDerma Azelac Face Scalp and Body Lotion", "Зволожуючий гель Sesderma Azelac Moisturizing Facial Gel", False),
    ("Sesderma C-Vit 5 Liposome Serum", "Sesderma C-Vit 5 Ліпосомальна сироватка з вітаміном С 30ml", True),
    ("Sesderma C-Vit 5 Liposome Serum", "Ліпосомальна сироватка з вітаміном C Sesderma C-Vit Liposomal Serum 30 мл", False),
    ("SeSDerma C-Vit Facial Liposomal Serum", "Sesderma C-Vit 5 Ліпосомальна сироватка з вітаміном С 30ml", False),
    ("SeSDerma C-Vit Facial Liposomal Serum", "Ліпосомальна сироватка з вітаміном C Sesderma C-Vit Liposomal Serum 30 мл", True),
    ("Sesderma Hidraven Foamy Soapless Cream", "Пінистий гель для вмивання обличчя і тіла SesDerma Laboratories Hidraven Foamy Soapless Cream", True),
    ("SeSDerma Reti-Age Eye Contour Gel", "Антивіковий гель для шкіри навколо очей Sesderma Reti Age Facial Eye Contour Gel 3-Retinol System 15 мл", True),
    ("Sesderma Reti Age 5 Liposomal Serum", "Sesderma Reti Age Serum", False),
    ("SeSDerma Reti-Age Antiaging Serum", "Крем-гель проти зморщок SesDerma Laboratories Reti Age Anti-Aging Gel", False),
    ("SeSDerma Reti-Age Antiaging Serum", "Антивікова сироватка для обличчя з трьома видами ретинолу Sesderma Reti Age Serum", True),
    ("Sesderma Laboratories Reti Age Facial Antiaging Cream 3-Retinol System", "Антивіковий зволожуючий крем для обличчя з ретинолом Sesderma Reti Age Facial Antiaging Cream", True),
    ("Sesderma Laboratories Reti Age Facial Antiaging Cream 3-Retinol System", "Антивікова сироватка для обличчя Sesderma Reti Age Facial Antiaging Serum 30 мл", False),
    ("SeSDerma C-Vit Intensive Serum Flash Effect", "Інтенсивна сироватка миттєвої дії Sesderma C-Vit Intensive Serum 10x1,5 мл", True),
    ("Sesderma C-Vit CC Cream SPF 15", "Антиоксидантный сс крем Sesderma C-VIT CC Cream SPF 15", True),
    ("Sesderma Azelac Ru Luminous fluid cream SPF 50", "Флюїд для сяйва шкіри Azelac Ru Depigmenting Luminous Fluid Cream SPF50 50 мл SeSDerma", True),
    ("Sesderma Azelac Ru Luminous fluid cream SPF 50", "Ліпосомальна сироватка з азелаїновою кислотою Sesderma Azelac Ru Liposomal Serum", False),
    ("SeSDerma Azelac Ru Depigmenting Ampoules", "Депігментувальний крем-гель для обличчя Sesderma Azelac Ru Cream Gel 50 мл", False),
    ("Sesderma FACTOR G Renew Eye Contour Cream", "Крем для контуру очей Sesderma Factor G Renew Eye Contour 15 мл", True),
    ("Sesderma FACTOR G Renew Eye Contour Cream", "Омолоджуючий крем Sesderma Factor G Renew Facial Cream 50 мл", False),
    ("Sesderma Factor G Renew Biostimulating Ampoules", "Омолоджуючий крем для обличчя з факторами росту Sesderma Factor G Renew", False),
    ("SeSDerma Atopises Moisturizing Cream", "Зволожуюча олія для чутливої шкіри Sesderma Atopises Oil", False),
    ("SeSDerma Atopises Moisturizing Cream", "Зволожувальний крем Sesderma Atopises Moisturizing Cream 50 мл", True),
    ("SesDerma Acglicolic 20 Nutritive Cream", "Зволожувальний крем-гель Sesderma Acglicolic 20 Moisturizing Cream Gel 50 мл", False),
    ("SeSDerma BTSeS Anti-wrinkle Cream", "Увлажняющий крем против морщин SesDerma BTSES Anti Wrinkle Moisturizing Cream", True),
    ("SeSDerma Daeses Firming Mask", "Маска с лифтинговым эффектом Sesderma Daeses Firming Mask", True),
    ("SeSDerma Daeses Firming Body Milk", "Крем-гель для обличчя Sesderma Daeses Face Firming Cream Gel", False),
    ("SeSDerma Daeses Firming Neck Gel", "Крем-гель для обличчя Sesderma Daeses Face Firming Cream Gel", False),
    ("SeSDerma Daeses Immediate Lifting Effect Serum", "Ліфтінг-крем для обличчя Sesderma Daeses Lifting Cream", False),
    ("SesDerma Abradermol Volcanic Exfoliating Scrub", "Скраб для обличчя та тіла SesDerma Laboratories Abradermol Volcanic Exfoliating Scrub", True),
    ("SesDerma Abradermol Volcanic Exfoliating Scrub", "Крем-скраб для мікродермабразії шкіри Sesderma Abradermol Microdermabrasion Cream 50 мл", False),
    ("SesDerma C-Vit Revitalizing Cream Gel", "Харчова добавка Sesderma C-VIT Defense", False),
    ("SesDerma C-Vit Revitalizing Cream Gel", "Крем-гель с витамином C Sesderma C-VIT Revitalizing Gel Cream", True),
    ("Sesderma Laboratories C-Vit Radiance Serum", "Флюїд для сяйва шкіри Sesderma C-Vit Radiance Glowing Fluid", False),
    ("SeSDerma Hidraderm Hyal Liposomal Serum", "Зволожуючий крем для обличчя Sesderma Hidraderm Hyal Facial Cream", False),
    ("SeSDerma Hidraderm Hyal Liposomal Serum", "Липосомальная сыворотка SesDerma Hidraderm Hyal Liposomal Serum", True),
    ("SesDerma Repaskin Dry Touch SPF 50", "Сонцезахисний крем для обличчя SesDerma Laboratories Repaskin Silk Touch Facial SPF 50", False),
    ("SeSDerma Men Hydrating Facial Lotion", "SeSDerma Зволожувальний лосьйон для обличчя для чоловіків Men Hydra Boost Lotion", False),
    ("SeSDerma Sebovalis Hair Solution", "Гель для жирної та проблемної шкіри обличчя SesDerma Laboratories Sebovalis Facial Gel", False),
    ("SeSDerma Hidraloe Body Milk", "Заспокійливий гель для обличчя та тіла з алое Sesderma Hidraloe Aloe Gel", False),
    ("Sesderma SENSYSES CLEANSER CLASSIC", "Тонізуючий засіб з гіалуроновою кислотою Sesderma Sensyses Cleanser Hyaluronic", False),
    ("SeSDerma Retises 0,5% Cream Forte", "Регенеруючий крем проти зморшок Sesderma Retises Antiwrinkle Regenerative Cream", False),
    ("SeSDerma Retises 0,5% Cream Forte", "Регенеруючий крем Sesderma Retises 0,5% Antiwrinkle Regenerative Cream Forte", True),
    ("SeSDerma Retises 0,5% Cream Forte", "Регенеруючий крем Sesderma Retises 1% Antiwrinkle Regenerative Cream Forte", False),
    ("Sesderma Seslash", "Сесдерма Seslash Туш-активатор росту вій Seslash Black 5 мл SeSDerma", False),
    ("Sesderma Seslash", "Сироватка-активатор росту вій та брів Sesderma SesLash Lash & Eyebrow Growth-Booster", True),
    ("SeSDerma Fillderma Nano Wrinkle Filler System", "Двокрокова система заповнення зморщок Sesderma Fillderma Nano Wrinkle Filling System 30+30 мл", True),
    ("SesDerma Cicases Wh Epithelializing Cream", "Епітелізувальний крем Sesderma Cicases Epithelizing Cream 30 мл", True),
    ("SeSDerma HIDRADERM Cleansing Milk pH 6,0", "Очищувальне молочко Sesderma Hidraderm Cleansing Milk 200 мл", True),
    ("SeSDerma Hidraderm Oatmeal & Rose Wate", "Тонік для чутливої шкіри Sesderma Hidraderm Oatmeal & Rose Water 200 мл", True),
    ("SeSDerma Hidraderm Oatmeal & Rose Wate", "Зволожувальний крем для обличчя Hidraderm Hyal Cream, 50 ml SeSDerma", False),
    ("Sesderma Lipocomal Ferulac Mist", "Ліпосомальний міст із феруловою кислотою Sesderma Ferulac Liposomal Mist 20 мл", True),
    ("SeSDerma Repaskin Transparent Spray SPF 50", "Сонцезахисний спрей для тіла Sesderma Repaskin Transparent Spray SPF 50+ 200 ml", True),
    ("SeSDerma Factor G Renew Cream", "Омолаживающий крем для лица Sesderma Factor G Renew Rejuvenating Cream", True),
    # Унікальна назва без форми в запиті: магазин дописує «спрей для тіла» — це той самий SKU.
    ("Acnemy Zitback", "Спрей для тіла проти акне Zitback 80 мл · Acnemy", True),
    ("Acnemy Zitback", "Спрей для тіла з акне Acnemy Zitback", True),
    ("Acnemy Zitback", "Acnemy ZITBACK Спрей для тіла з акне 80 ml", True),
    ("Acnemy Zitback", "Acnemy Zitcontrol крем від акне", False),
    ("Acnemy Zitback", "Спрей для тіла проти акне Acnemy Zitback Body Spray For Blemish-Prone Skin 80 мл", True),
    ("Acnemy Zitback", "Крем для повік Acnemy Zitback Eye Contour", False),
    ("SeSDerma Daeses Firming Mask", "Молочко для тіла Sesderma Daeses Firming Body Milk", False),
    ("Moroccanoil Treatment", "Moroccanoil Treatment Mist", False),
    # Кириличне «нічна» в описі під повний латинський ключ — той самий SKU (Pleka/Fabo).
    ("MEDIK8 Crystal Retinal 20", "MEDIK8 CRYSTAL RETINAL 20 30ml - Нічна сироватка зі стабільним ретиналем", True),
    ("Medik8 Crystal Retinal", "Medik8 – Crystal Retinal – Нічна крем-сироватка з ретиналем", True),
    ("Medik8 Crystal Retinal Day Serum", "Medik8 Crystal Retinal – Нічна сироватка з ретиналем", False),
    # Academie: UA-магазини часто без французької назви — збіг через укр. опис / артикул.
    (
        "Academie PHYTO-GOMMAGE MARIN Рослинний пілінг з водоростями 50 мл 02261",
        "Рослинний пілінг з морськими водоростями Academie 50ml",
        True,
    ),
    (
        "Academie PHYTO-GOMMAGE MARIN Рослинний пілінг з водоростями 50 мл",
        "Academie Phyto-Gommage Marin рослинний пілінг 50 мл",
        True,
    ),
    (
        "Academie Visage LAIT HYPO-SENSIBLE Гіпоаллергенне молочко 200ml 03047-1444",
        "Гіпоалергенне молочко Academie Visage 200 мл",
        True,
    ),
    (
        "Academie Visage LAIT HYPO-SENSIBLE Гіпоаллергенне молочко 200ml",
        "Academie Visage Tonique Hypo-Sensible гіпоалергенний тонік 200 мл",
        False,
    ),
    (
        "Academie Youth-Repair Soin Hydratant Regenerant Зволожуючий регенеруючий догляд 50 мл",
        "Academie Youth Repair Soin Hydratant Regenerant 50ml",
        True,
    ),
    (
        "Academie Derm Acte Collagen to Drink Питний колаген 195 мл",
        "Academie Derm Acte Collagen to Drink 195ml",
        True,
    ),
    (
        "Academie Creme Veloutee М'який зволожуючий крем 50 мл",
        "Крем для тіла Academie Body 200 мл",
        False,
    ),
    # Французькі діакритики в бренді/назві (Académie) не повинні ламати матч.
    (
        "Académie Visage Lait Hypo-Sensible 200 ml",
        "Гіпоалергенне молочко Academie Visage Hypo-Sensible 200 мл",
        True,
    ),
    (
        "Academie Hypo-Sensible Universal Cream",
        "Універсальний захисний крем / Hypo Sensible Academie Hypo-Sensible Universal Cream",
        True,
    ),
    # Dermal Cosmetics: EN title на картці.
    (
        "Academie Mattifying Hydrating Balm Матуючий зволожуючий бальзам",
        "Academie Mattifying Hydrating Balm",
        True,
    ),
  # Лінійки й опечатки в картках нових магазинів.
    ("Medik8 Total Moisture", "Щоденний зволожуючий крем Medik8 Total Moitur", True),
    ("Rejuran Essential Hydration Cream 50 ml", "Живильний крем Rejuran Nutritive Cream 50 мл", False),
    ("Rejuran Essential Hydration Cream 50 ml", "Rejuran Essential Hydration Cream 50 ml", True),
    ("Valmont Hydra3 Charging Cream", "Valmont Hydra3 Regenetic Cream 50ml", False),
    ("Valmont Hydra3 Charging Cream", "Енергетичний зволожуючий крем Valmont Hydra3 Charging Cream", True),
    (
        "HydroPeptide Solar Dew",
        "HydroPeptide Solar Dew SPF 30 Сонцезахисна сироватка",
        True,
    ),
]


def score(query: str, title: str) -> float:
    product = finalize_product(
        {"title": title, "price": 1000, "url": "", "options": [],
         "spf": extract_specs(title)["spf"], "volume_match": "unknown"},
        query,
    )
    return candidate_score(query, product)


if __name__ == "__main__":
    bad = 0
    for query, title, expected in CASES:
        s = score(query, title)
        ok = (s >= THRESHOLD) == expected
        bad += not ok
        if not ok or "-v" in sys.argv:
            print(f"{'OK ' if ok else 'BAD'} {s:.2f} очік={'так' if expected else 'ні '} | {query[:44]:44} | {title[:80]}")
    print(f"{len(CASES) - bad}/{len(CASES)} пар проходять")
    sys.exit(1 if bad else 0)
