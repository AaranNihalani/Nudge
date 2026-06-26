"""Generate datasets/thailand_lender_tiers.csv — all 77 provinces × multiple lenders.
Run with: .venv/bin/python scripts/generate_thailand_lenders.py
"""
import csv, pathlib

PROVINCES = [
    "Amnat Charoen", "Ang Thong", "Bangkok", "Bueng Kan", "Buri Ram",
    "Chachoengsao", "Chai Nat", "Chaiyaphum", "Chanthaburi", "Chiang Mai",
    "Chiang Rai", "Chon Buri", "Chumphon", "Kalasin", "Kamphaeng Phet",
    "Kanchanaburi", "Khon Kaen", "Krabi", "Lampang", "Lamphun",
    "Loei", "Lop Buri", "Mae Hong Son", "Maha Sarakham", "Mukdahan",
    "Nakhon Nayok", "Nakhon Pathom", "Nakhon Phanom", "Nakhon Ratchasima",
    "Nakhon Sawan", "Nakhon Si Thammarat", "Nan", "Narathiwat", "Nong Bua Lam Phu",
    "Nong Khai", "Nonthaburi", "Pathum Thani", "Pattani", "Phang Nga",
    "Phatthalung", "Phayao", "Phetchabun", "Phetchaburi", "Phichit",
    "Phitsanulok", "Phra Nakhon Si Ayutthaya", "Phrae", "Phuket",
    "Prachin Buri", "Prachuap Khiri Khan", "Ranong", "Ratchaburi",
    "Rayong", "Roi Et", "Sa Kaeo", "Sakon Nakhon", "Samut Prakan",
    "Samut Sakhon", "Samut Songkhram", "Sara Buri", "Satun", "Sing Buri",
    "Sisaket", "Songkhla", "Sukhothai", "Suphan Buri", "Surat Thani",
    "Surin", "Tak", "Trang", "Trat", "Ubon Ratchathani", "Udon Thani",
    "Uthai Thani", "Uttaradit", "Yala", "Yasothon",
]

URBAN = {
    "Bangkok", "Nonthaburi", "Pathum Thani", "Samut Prakan", "Samut Sakhon",
    "Samut Songkhram", "Chiang Mai", "Chon Buri", "Khon Kaen",
    "Nakhon Ratchasima", "Udon Thani", "Surat Thani", "Phuket", "Songkhla",
    "Rayong", "Chachoengsao", "Phra Nakhon Si Ayutthaya",
}

# Provinces with no or very few Pico Finance operators
PICO_EXCLUDED = {"Mae Hong Son", "Ranong"}

# Pueantae Quick Money branches (named Pico operator at 7.2% APR — far below the cap)
PUEANTAE_PROVINCES = {"Bangkok", "Pathum Thani", "Chon Buri", "Rayong"}

# Maccabee Group — Bangkok + Khon Kaen + Roi Et
MACCABEE_PROVINCES = {"Bangkok", "Khon Kaen", "Roi Et"}

# Sahapaibuul 2558 — Northeast only
SAHAPAIBUUL_PROVINCES = {"Roi Et", "Maha Sarakham", "Kalasin", "Khon Kaen"}

# Thai Ace Capital — Bangkok + Nakhon Ratchasima
THAI_ACE_PROVINCES = {"Bangkok", "Nakhon Ratchasima"}

# M Capital Corporation — Bangkok metro
M_CAPITAL_PROVINCES = {"Bangkok", "Nonthaburi", "Pathum Thani", "Samut Prakan"}

ROWS = []

for prov in PROVINCES:
    # ── 1. Village Fund ───────────────────────────────────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "Village Fund (กองทุนหมู่บ้าน / TVURF)",
        "rate_apr": 6.00,
        "effective_date": "2024-01-01",
        "source": (
            "National Village and Urban Community Fund (NVUCFO). ~79,610 village funds nationwide. "
            "Mean rate ~6% p.a. (World Bank / MIT study). Typical limit ฿10,000–20,000 for "
            "registered community members. Find yours at villagefund.or.th"
        ),
    })

    # ── 2. BAAC Half-Half (subsidised, farmers only) ──────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "BAAC Half-Half Loan (ธ.ก.ส. กึ่งดอก) — registered farmers only",
        "rate_apr": 3.00,
        "effective_date": "2024-01-01",
        "source": (
            "BAAC. Borrower pays 3%; govt subsidises 3% to total 6%. 7 crop categories. "
            "Limit ฿100,000. Requires BAAC account + farmer registration. To April 2029. "
            "Source: Bangkok Post / BAAC."
        ),
    })

    # ── 3. BAAC Standard ──────────────────────────────────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "BAAC Standard Loan (ธ.ก.ส.)",
        "rate_apr": 6.625,
        "effective_date": "2026-01-01",
        "source": (
            "Bank for Agriculture and Agricultural Cooperatives (BAAC). "
            "MRR 6.625% p.a. effective 1 Jan 2026 (cut 0.25% from 6.875%). "
            "Farmers and rural borrowers; nationwide. baac.or.th"
        ),
    })

    # ── 4. Thai Credit Bank — Nano (28%, up to ฿30k, no collateral/guarantor) ─
    ROWS.append({
        "district": prov,
        "lender": "Thai Credit Bank — Nano Loan (ไทยเครดิต นาโน)",
        "rate_apr": 28.00,
        "effective_date": "2024-01-01",
        "source": (
            "Thai Credit Bank PCL (ธนาคารไทยเครดิต บมจ.) — Thailand's only commercial bank "
            "focused on nano/micro-SME lending. Nano loan: up to ฿30,000 at max 28% p.a. "
            "No collateral; no guarantor; 6–12 months. 267 branches nationwide. "
            "Also serves online merchants, OTOP entrepreneurs. thaicreditbank.com"
        ),
    })

    # ── 5. Thai Credit Bank — Micro Credit (28%, up to ฿200k) ───────────────
    ROWS.append({
        "district": prov,
        "lender": "Thai Credit Bank — Micro Credit (ไทยเครดิต ไมโคร)",
        "rate_apr": 28.00,
        "effective_date": "2024-01-01",
        "source": (
            "Thai Credit Bank PCL — Micro Credit: up to ฿200,000 at max 28% p.a. "
            "No collateral; no guarantor required. Targets micro-SME, freelancers, "
            "online sellers. 267 branches nationwide. thaicreditbank.com"
        ),
    })

    # ── 6. Good Money by GSB — Personal Loan (19–25% APR, up to ฿1M) ────────
    ROWS.append({
        "district": prov,
        "lender": "Good Money by GSB — Personal Loan (เงินดีดี สินเชื่อส่วนบุคคล)",
        "rate_apr": 22.00,
        "effective_date": "2024-01-01",
        "source": (
            "Good Money (เงินดีดี) — subsidiary of Government Savings Bank. "
            "Personal loan: 19–25% p.a. (mid-estimate 22%); up to ฿1,000,000. "
            "App-based (30-min approval). Specifically targets low-income and informal workers "
            "who cannot access standard bank products. All occupations eligible. "
            "gsb.or.th/personal/good-money/"
        ),
    })

    # ── 7. Krungsri First Choice PayPlus (no income docs, up to ฿20k) ───────
    ROWS.append({
        "district": prov,
        "lender": "Krungsri First Choice PayPlus — no income docs, up to ฿20,000",
        "rate_apr": 25.00,
        "effective_date": "2024-01-01",
        "source": (
            "Krungsri First Choice (บจ. อยุธยา แคปปิตอล เซอร์วิสเซส, subsidiary of Bank of Ayudhya / Krungsri). "
            "PayPlus: digital loan using alternative data — NO income documents required. "
            "Credit limit up to ฿20,000; up to 5-month repayment. "
            "Rate est. ~25% p.a. for non-bank personal loan (BOT ceiling). "
            "Targets self-employed, freelancers, informal workers. "
            "Source: moneyandbanking.co.th 2024; krungsri.com"
        ),
    })

    # ── 8. SCB UP — revolving, no income proof (฿1,000+) ────────────────────
    ROWS.append({
        "district": prov,
        "lender": "SCB UP — revolving credit, no income proof (ธนาคารไทยพาณิชย์)",
        "rate_apr": 25.00,
        "effective_date": "2024-01-01",
        "source": (
            "SCB UP (UP-Ngern-Yuem) — revolving personal loan for people WITHOUT fixed income "
            "or proof of income. Min credit line ฿1,000. Builds credit history for first-time borrowers. "
            "Rate est. ~25% p.a. (BOT non-bank personal loan ceiling). "
            "Source: scb.co.th/en/personal-banking/loans/up/up-ngern-yuem"
        ),
    })

    # ── 9. Good Money by GSB — Nano (29–33% APR, targets informal workers) ──
    ROWS.append({
        "district": prov,
        "lender": "Good Money by GSB — Nano Finance (เงินดีดี นาโน)",
        "rate_apr": 29.00,
        "effective_date": "2024-01-01",
        "source": (
            "Good Money (เงินดีดี) — GSB subsidiary. BOT Nano Finance licence. "
            "29–33% p.a. (all-in effective); up to ฿100,000; no collateral. "
            "App-based, 30-min approval. Specifically designed for low-income and "
            "informal-sector workers who lack standard income documents. "
            "All occupations eligible. gsb.or.th/personal/good-money/"
        ),
    })

    # ── 10. Saksiam Leasing — Nano Finance ────────────────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "Saksiam Leasing — Nano Finance (ศักดิ์สยาม ลิสซิ่ง)",
        "rate_apr": 30.00,
        "effective_date": "2024-01-01",
        "source": (
            "Saksiam Leasing PCL (บมจ. ศักดิ์สยาม ลิสซิ่ง). BOT Nano Finance licence. "
            "27.96–33% p.a. all-in; up to ฿100,000. Ages 20–70; own business required; "
            "nationwide branches (HQ: Uttaradit). saksiam.com/service/nanofinance"
        ),
    })

    # ── 11. Siam Digital Lending — Nano ───────────────────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "Siam Digital Lending — Nano Finance (สยามดิจิทัล)",
        "rate_apr": 30.00,
        "effective_date": "2024-01-01",
        "source": (
            "Siam Digital Lending Co., Ltd. BOT Nano Finance licence. ~30% p.a. all-in. "
            "฿5,000–100,000; 6–36 months. Min income ฿12,000/month; no collateral. "
            "Digital application at siamdl.co.th"
        ),
    })

    # ── 12. TIDLOR — Vehicle title loan (requires vehicle) ───────────────────
    ROWS.append({
        "district": prov,
        "lender": "TIDLOR — Vehicle Title Loan (เงินติดล้อ) — requires vehicle",
        "rate_apr": 18.00,
        "effective_date": "2024-01-01",
        "source": (
            "TIDLOR PCL (บมจ. เงินติดล้อ). BOT-licensed finance company. "
            "Vehicle title loan (car/motorcycle/truck): 12–24% p.a. effective (mid-estimate 18%). "
            "Ages 21–68; vehicle owner; 6+ months employment or business. "
            "Accepts employees, self-employed, AND freelancers — but requires a vehicle as title. "
            "tidlor.com/en/loan/car"
        ),
    })

    # ── 13. GSB People's Bank Loan (est. ~25% APR flat) ──────────────────────
    ROWS.append({
        "district": prov,
        "lender": "GSB People's Bank Loan (ธนาคารออมสิน — สินเชื่อธนาคารประชาชน)",
        "rate_apr": 25.00,
        "effective_date": "2024-01-01",
        "source": (
            "Government Savings Bank (GSB). People's Bank Loan: limit ฿200,000 per person. "
            "Fixed flat rate — exact rate not published (est. 12–15% flat ≈ 25% APR effective); "
            "verify at gsb.or.th or any GSB branch. "
            "Repayment: 3 yrs (≤฿50k), 5 yrs (≤฿100k), 8 yrs (≤฿200k). "
            "Uses: working capital, living expenses, debt repayment. Needs guarantor or property. "
            "Rate is ESTIMATED — confirm before applying."
        ),
    })

    # ── 14. GSB Welfare Card Loan (~12% APR est., welfare card holders) ──────
    ROWS.append({
        "district": prov,
        "lender": "GSB Welfare Card Loan (ธนาคารออมสิน — บัตรสวัสดิการ)",
        "rate_apr": 12.00,
        "effective_date": "2024-01-01",
        "source": (
            "Government Savings Bank — Loan for State Welfare Card holders "
            "(บัตรสวัสดิการแห่งรัฐ). Est. ~12% p.a.; no guarantor required; 5-year repayment. "
            "Rate is estimated — verify at gsb.or.th. gsb.or.th"
        ),
    })

    # ── 15. Rabbit Cash — Nano (digital, targets freelancers/online sellers) ─
    ROWS.append({
        "district": prov,
        "lender": "Rabbit Cash — Nano Finance (digital, เงินกระต่าย)",
        "rate_apr": 33.00,
        "effective_date": "2024-01-01",
        "source": (
            "Rabbit Cash Co., Ltd. BOT Nano Finance licence. "
            "JV: BTS Group + AEON Thana Sinsap + Humanica. 100% digital. "
            "Specifically targets freelancers, online sellers, informal workers "
            "WITHOUT standard income documents. Up to ฿100,000; no collateral. "
            "Max 33% p.a. all-in. Source: btsgroup.co.th / BOT"
        ),
    })

    # ── 16. AIRA & AIFUL — Nano Finance ──────────────────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "AIRA & AIFUL — Nano Finance",
        "rate_apr": 33.00,
        "effective_date": "2024-01-01",
        "source": (
            "AIRA & AIFUL PCL (บมจ. ไอร่า แอนด์ ไอฟุล). BOT Nano Finance licence. "
            "Max 33% p.a. all-in; up to ฿100,000. 45 branches nationwide. "
            "Requires: 1+ yr business, guarantor with ≥฿9,000/mo income, age 20–60. "
            "Source: amoney.co.th/news/detail/358"
        ),
    })

    # ── 17. Muangthai Capital — Nano Finance ─────────────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "Muangthai Capital — Nano Finance (เมืองไทย แคปปิตอล)",
        "rate_apr": 28.00,
        "effective_date": "2024-01-01",
        "source": (
            "Muangthai Capital PCL (MTC.BK, SET-listed). BOT Nano Finance licence. "
            "28–33% p.a. all-in; ฿21,000–40,000; 10–50 month terms. "
            "One of Thailand's largest nano finance operators. Nationwide branches. "
            "muangthaicap.com/en/loan/nano-finance/"
        ),
    })

    # ── 18. Srisawad Corporation — Nano Finance ───────────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "Srisawad Corporation — Nano Finance (ศรีสวัสดิ์)",
        "rate_apr": 33.00,
        "effective_date": "2024-01-01",
        "source": (
            "Srisawad Corporation PCL (SAWAD.BK, SET-listed). BOT Nano Finance licence. "
            "Max 33% p.a. all-in; up to ฿100,000; no collateral. "
            "Large nationwide branch and agent network. Source: SET / SCBEIC"
        ),
    })

    # ── 19. Nano Finance — BOT cap (generic) ─────────────────────────────────
    ROWS.append({
        "district": prov,
        "lender": "Nano Finance — BOT-licensed (any operator)",
        "rate_apr": 33.00,
        "effective_date": "2024-01-01",
        "source": (
            "Bank of Thailand Nano Finance (BOT Notification No. 2/2558, 2015). "
            "Max effective rate 33% p.a. all-in (interest + fees + insurance). "
            "No collateral; limit ฿100,000; business purpose only. "
            "~55–70 licensed operators. Find providers at app.bot.or.th/botlicensecheck"
        ),
    })

    # ── 20. Pico Finance — generic FPO cap ───────────────────────────────────
    if prov not in PICO_EXCLUDED:
        ROWS.append({
            "district": prov,
            "lender": "Pico Finance (FPO — พิโกไฟแนนซ์)",
            "rate_apr": 36.00,
            "effective_date": "2024-10-01",
            "source": (
                "Fiscal Policy Office (FPO / สำนักงานเศรษฐกิจการคลัง), Ministry of Finance. "
                "Max 36% p.a. all-in; limit ฿50,000 per borrower. "
                "1,155 active operators in 75 provinces (May 2025). Province-restricted lending. "
                "NPL rate 23.4% (Q1 2025). Find operators at 1359.go.th/picodoc/pico_public/ "
                "or search 'พิโกไฟแนนซ์ [จังหวัด]' online."
            ),
        })
        ROWS.append({
            "district": prov,
            "lender": "Pico Plus (FPO — พิโกพลัส)",
            "rate_apr": 36.00,
            "effective_date": "2024-10-01",
            "source": (
                "FPO Pico Plus tier. Max 36% p.a. all-in; limit ฿100,000 (vs ฿50k standard). "
                "Higher capital requirement (฿10M vs ฿5M). For borrowers with repayment history. "
                "Province-restricted. Pending reform to allow adjacent provinces. "
                "Source: FPO / Nation Thailand (May 2025)."
            ),
        })

    # ── 21. Krungthai Smart Money (urban, min income ฿30k) ───────────────────
    if prov in URBAN:
        ROWS.append({
            "district": prov,
            "lender": "Krungthai Smart Money (กรุงไทย) — min income ฿30,000/month",
            "rate_apr": 18.00,
            "effective_date": "2024-01-01",
            "source": (
                "Krungthai Bank (KTB). Smart Money personal loan: 18% p.a.; up to ฿1,000,000. "
                "IMPORTANT: Requires minimum monthly income of ฿30,000 — "
                "excludes most informal-sector and low-income borrowers. "
                "krungthai.com/en/personal/loan/personal-loan/126"
            ),
        })

    # ── Province-specific named operators ─────────────────────────────────────

    if prov in PUEANTAE_PROVINCES:
        ROWS.append({
            "district": prov,
            "lender": "Pueantae Quick Money — Pico Finance (เพื่อนแท้ ควิกมันนี่)",
            "rate_apr": 7.20,
            "effective_date": "2024-01-01",
            "source": (
                "Pueantae Quick Money (เพื่อนแท้ ควิกมันนี่). FPO Pico Finance licence No. ว00007/2565. "
                "Rate from 0.60%/month (7.2% p.a.) — far below the Pico cap of 36%. "
                "Branches in Bangkok (Don Mueang, Min Buri), Pathum Thani (Rangsit), "
                "Chon Buri, Rayong. Phone: 02-114-8988. "
                "Source: puean.co.th / 1359.go.th"
            ),
        })

    if prov in THAI_ACE_PROVINCES:
        ROWS.append({
            "district": prov,
            "lender": "Thai Ace Capital — Nano Finance (ไทยเอซ แคปปิตอล)",
            "rate_apr": 20.00,
            "effective_date": "2024-01-01",
            "source": (
                "Thai Ace Capital Co., Ltd. BOT Nano Finance licence. "
                "Max ~15% interest + 5% fee (~20% p.a. effective). "
                "Initially Bangkok + Nakhon Ratchasima; now accepts applications nationwide. "
                "tcapital.co.th"
            ),
        })

    if prov in MACCABEE_PROVINCES:
        ROWS.append({
            "district": prov,
            "lender": "Maccabee Group — Nano Finance (แมคคาเล กรุ๊พ)",
            "rate_apr": 33.00,
            "effective_date": "2024-01-01",
            "source": (
                "Maccabee Group Co., Ltd. BOT Nano Finance licence (2015 original licensee). "
                "Max 33% p.a. all-in; up to ฿100,000. "
                "Branches in Bangkok, Khon Kaen, Roi Et. "
                "Source: ryt9.com/s/iq03/2149075"
            ),
        })

    if prov in SAHAPAIBUUL_PROVINCES:
        ROWS.append({
            "district": prov,
            "lender": "Sahapaibuul 2558 — Nano Finance (สหไพบูลย์ 2558)",
            "rate_apr": 33.00,
            "effective_date": "2024-01-01",
            "source": (
                "Sahapaibuul 2558 Co., Ltd. BOT Nano Finance licence (2015 original licensee). "
                "Max 33% p.a. all-in; up to ฿100,000. "
                "Operates mainly in Northeast: Roi Et, Maha Sarakham, Kalasin, Khon Kaen. "
                "Source: ryt9.com/s/iq03/2149075"
            ),
        })

    if prov in M_CAPITAL_PROVINCES:
        ROWS.append({
            "district": prov,
            "lender": "M Capital Corporation — Nano Finance (เอ็ม แคปปิตอล)",
            "rate_apr": 33.00,
            "effective_date": "2024-01-01",
            "source": (
                "M Capital Corporation Co., Ltd. BOT Nano Finance licence. "
                "2.75%/month (33% p.a. all-in); up to ฿100,000. 6-month terms. "
                "Weekly/biweekly/monthly repayment options. Requires guarantor. "
                "Targets traders and merchants. Bangkok metro. mcapital.co.th"
            ),
        })


out = pathlib.Path(__file__).parent.parent / "datasets" / "thailand_lender_tiers.csv"
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["district", "lender", "rate_apr", "effective_date", "source"])
    w.writeheader()
    w.writerows(ROWS)

print(f"Written {len(ROWS)} rows to {out}")

from collections import Counter
by_type = Counter()
for r in ROWS:
    name = r["lender"].split("—")[0].strip().split("(")[0].strip()
    by_type[name] += 1
print(f"\n{'Lender':<60} {'Provinces':>10}")
print("-" * 72)
for name, n in sorted(by_type.items(), key=lambda x: -x[1]):
    print(f"  {name:<58} {n:>10}")
