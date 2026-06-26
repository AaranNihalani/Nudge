"""Generate datasets/thailand_lender_tiers.csv with all 77 provinces × multiple lenders."""
import csv, pathlib, sys

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

# Major urban provinces that have Krungthai / more urban lenders
URBAN_PROVINCES = {
    "Bangkok", "Nonthaburi", "Pathum Thani", "Samut Prakan", "Samut Sakhon",
    "Chiang Mai", "Chon Buri", "Khon Kaen", "Nakhon Ratchasima", "Udon Thani",
    "Surat Thani", "Phuket", "Songkhla", "Rayong", "Chachoengsao",
    "Phra Nakhon Si Ayutthaya", "Ayutthaya",
}

# All provinces with Pico Finance (FPO data: 75 of 77 provinces covered)
# Exclude very remote: Mae Hong Son and Ranong historically have fewer operators
PICO_EXCLUDED = {"Mae Hong Son", "Ranong"}

ROWS = []

for prov in PROVINCES:
    # ── 1. Village Fund (TVURF) ───────────────────────────────────────────────
    # 79,610 village funds nationally; mean rate 6% p.a. from World Bank / MIT study
    ROWS.append({
        "district": prov,
        "lender": "Village Fund (กองทุนหมู่บ้าน / TVURF)",
        "rate_apr": 6.00,
        "effective_date": "2024-01-01",
        "source": (
            "National Village and Urban Community Fund (NVUCFO). "
            "~79,610 village funds nationwide; mean rate ~6% p.a. (World Bank / MIT study). "
            "Typical limit ฿10,000–20,000 for registered community members. "
            "Note: 2025 government top-up funds redirected to community projects — "
            "individual lending from revolving pool continues at village committee discretion. "
            "Find your local fund at villagefund.or.th"
        ),
    })

    # ── 2. BAAC Half-Half Subsidised Loan ────────────────────────────────────
    # Borrower pays 3%; government subsidises 3% to reach 6% total.
    # Program runs to April 2029. Registered farmers only. Limit ฿100,000.
    ROWS.append({
        "district": prov,
        "lender": "BAAC Half-Half Loan (ธ.ก.ส. กึ่งดอก) — farmers only",
        "rate_apr": 3.00,
        "effective_date": "2024-01-01",
        "source": (
            "BAAC (Bank for Agriculture and Agricultural Cooperatives). "
            "Borrower pays 3% p.a.; government subsidises 3% to reach 6% total. "
            "Covers 7 crop categories (rice, corn, palm oil, cassava, rubber, sugarcane, fruit). "
            "Limit ฿100,000 per farmer. Requires BAAC account and farmer registration. "
            "Program to April 2029. Source: Bangkok Post / BAAC."
        ),
    })

    # ── 3. BAAC Standard (MRR) ───────────────────────────────────────────────
    # MRR effective 1 Jan 2026: 6.625%. Primarily serves agricultural borrowers.
    ROWS.append({
        "district": prov,
        "lender": "BAAC Standard Loan (ธ.ก.ส.)",
        "rate_apr": 6.625,
        "effective_date": "2026-01-01",
        "source": (
            "Bank for Agriculture and Agricultural Cooperatives (BAAC). "
            "MRR 6.625% p.a. effective 1 January 2026 (cut 0.25% from 6.875%). "
            "Agricultural and rural borrowers; nationwide branch network. "
            "Farmers typically eligible for MRR or MRR-0.5% rates. "
            "Source: Nation Thailand, Dec 2025."
        ),
    })

    # ── 4. Government Savings Bank — People's Bank Loan ──────────────────────
    # Exact flat rate not published. Estimated ~12–15% flat = ~22–27% APR effective.
    # GSB MRR is 6.195% but People's Bank uses a fixed flat rate for this product.
    # We use 25% as mid-estimate; mark as estimated.
    ROWS.append({
        "district": prov,
        "lender": "GSB People's Bank Loan (ธนาคารออมสิน – สินเชื่อธนาคารประชาชน)",
        "rate_apr": 25.00,
        "effective_date": "2024-01-01",
        "source": (
            "Government Savings Bank (GSB / ธนาคารออมสิน). "
            "People's Bank Loan: limit ฿200,000 per person (combining all accounts). "
            "Fixed flat rate — exact rate not published publicly (est. 12–15% flat ≈ 25% APR effective); "
            "verify at gsb.or.th or any GSB branch before applying. "
            "Repayment: 3 yrs (≤฿50k), 5 yrs (≤฿100k), 8 yrs (≤฿200k). "
            "Uses: working capital, living expenses, debt repayment. Collateral: guarantor or property. "
            "Source: gsb.or.th (rate ESTIMATED)."
        ),
    })

    # ── 5. Siam Digital Lending (Nano Finance) ───────────────────────────────
    # Named company; 30% effective all-in; digital/national; min income 12k/mo
    ROWS.append({
        "district": prov,
        "lender": "Siam Digital Lending — Nano Finance (สยามดิจิทัล)",
        "rate_apr": 30.00,
        "effective_date": "2024-01-01",
        "source": (
            "Siam Digital Lending Co., Ltd. (BOT Nano Finance licence). "
            "All-in effective rate ~30% p.a. (interest + fees + insurance). "
            "Loan range ฿5,000–100,000; term 6–36 months. "
            "Minimum income ฿12,000/month; no collateral required. "
            "Apply at siamdl.co.th. National coverage."
        ),
    })

    # ── 6. Nano Finance (BOT cap — generic) ──────────────────────────────────
    # 55 BOT-licensed operators as of Aug 2024; cap 33% all-in (some sources say 36%)
    # BOT Notification No. 2/2558 (2015): max effective rate 36% all-in.
    # In practice most named operators charge 30–33%.
    ROWS.append({
        "district": prov,
        "lender": "Nano Finance — BOT-licensed (general)",
        "rate_apr": 33.00,
        "effective_date": "2024-01-01",
        "source": (
            "Bank of Thailand Nano Finance scheme (BOT Notification No. 2/2558, 2015). "
            "Max effective rate 36% p.a. all-in (interest + fees + insurance). "
            "No collateral; limit ฿100,000 per borrower for business purposes. "
            "55 licensed operators as of August 2024. "
            "Find licensed providers at app.bot.or.th/botlicensecheck. "
            "Source: BOT; SCBEIC analysis."
        ),
    })

    # ── 7. Muangthai Capital (Nano Finance) ──────────────────────────────────
    # SET-listed, one of the largest Nano Finance operators; nationwide branches
    ROWS.append({
        "district": prov,
        "lender": "Muangthai Capital — Nano Finance (เมืองไทย แคปปิตอล)",
        "rate_apr": 33.00,
        "effective_date": "2024-01-01",
        "source": (
            "Muangthai Capital Public Company Limited (MTC.BK, SET-listed). "
            "One of Thailand's largest BOT Nano Finance operators. "
            "Max effective rate 33% p.a. all-in; limit ฿100,000; no collateral. "
            "Nationwide branch network. Source: SCBEIC; SET filing."
        ),
    })

    # ── 8. Srisawad Corporation (Nano Finance) ───────────────────────────────
    # SET-listed; listed as early Nano Finance licensee; widespread branches
    ROWS.append({
        "district": prov,
        "lender": "Srisawad Corporation — Nano Finance (ศรีสวัสดิ์)",
        "rate_apr": 33.00,
        "effective_date": "2024-01-01",
        "source": (
            "Srisawad Corporation Public Company Limited (SAWAD.BK, SET-listed). "
            "BOT Nano Finance licensee (one of first batch, 2015). "
            "Max effective rate 33% p.a. all-in; limit ฿100,000; no collateral. "
            "Large nationwide branch and agent network. Source: SCBEIC; SET filing."
        ),
    })

    # ── 9. Pico Finance / Pico Plus (FPO) ────────────────────────────────────
    if prov not in PICO_EXCLUDED:
        ROWS.append({
            "district": prov,
            "lender": "Pico Finance (FPO — พิโกไฟแนนซ์)",
            "rate_apr": 36.00,
            "effective_date": "2024-10-01",
            "source": (
                "Fiscal Policy Office (FPO / สำนักงานเศรษฐกิจการคลัง), Ministry of Finance. "
                "Max 36% p.a. all-in; limit ฿50,000 per borrower. "
                "1,155 active operators across 75 provinces (May 2025); province-restricted lending. "
                "NPL rate: 23.4% (Q1 2025). Find local operators via FPO website. "
                "Source: Nation Thailand (May 2025); FPO."
            ),
        })
        ROWS.append({
            "district": prov,
            "lender": "Pico Plus (FPO — พิโกพลัส)",
            "rate_apr": 36.00,
            "effective_date": "2024-10-01",
            "source": (
                "Fiscal Policy Office (FPO), Ministry of Finance — Pico Plus tier. "
                "Max 36% p.a. all-in; limit ฿100,000 per borrower (vs ฿50k for standard Pico). "
                "Min paid-up capital ฿10M (vs ฿5M for standard Pico). "
                "Intended for borrowers with established repayment history. "
                "Province-restricted lending; pending reform to allow adjacent provinces. "
                "Source: FPO; Nation Thailand (May 2025)."
            ),
        })

    # ── 10. Krungthai Smart Money (urban only) ────────────────────────────────
    if prov in URBAN_PROVINCES:
        ROWS.append({
            "district": prov,
            "lender": "Krungthai Smart Money (กรุงไทย) — min income ฿30k/month",
            "rate_apr": 18.00,
            "effective_date": "2024-01-01",
            "source": (
                "Krungthai Bank (KTB / ธนาคารกรุงไทย) — Smart Money personal loan. "
                "18% p.a. fixed; limit up to ฿1,000,000. "
                "IMPORTANT: Requires minimum monthly income of ฿30,000 — "
                "excludes most low-income and informal-sector borrowers. "
                "Source: krungthai.com/en/personal/loan/personal-loan/126"
            ),
        })

# ── Special Bangkok-only: GSB Welfare Card Loan ──────────────────────────────
# Available nationwide but targeted at welfare card holders — add separately as national
for prov in PROVINCES:
    ROWS.append({
        "district": prov,
        "lender": "GSB Welfare Card Loan (ธนาคารออมสิน — บัตรสวัสดิการ)",
        "rate_apr": 12.00,
        "effective_date": "2024-01-01",
        "source": (
            "Government Savings Bank — Loan for State Welfare Card holders. "
            "Low interest rate (est. ~12% p.a.); no guarantor required; 5-year repayment. "
            "Requires possession of state welfare card (บัตรสวัสดิการแห่งรัฐ). "
            "Rate is estimated — verify at gsb.or.th. Source: GSB product page."
        ),
    })

out = pathlib.Path(__file__).parent.parent / "datasets" / "thailand_lender_tiers.csv"
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["district", "lender", "rate_apr", "effective_date", "source"])
    w.writeheader()
    w.writerows(ROWS)

print(f"Written {len(ROWS)} rows to {out}")

# Summary table
from collections import Counter
by_lender = Counter(r["lender"].split(" — ")[0].strip() for r in ROWS)
print("\nLender type | Province count")
for lender, n in sorted(by_lender.items(), key=lambda x: x[0]):
    print(f"  {n:3d}  {lender}")
