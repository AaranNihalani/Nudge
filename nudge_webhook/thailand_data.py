"""
Thailand data configuration for Nudge Microfinance.

All statistics are sourced from published peer-reviewed papers (not original analysis),
because primary microdata (NSO SES 2021, Townsend Thai Project) was access-gated
at build time. See analysis/thailand/data_access_log.md.

When real microdata is obtained and analyse_thailand.py has been run, this module
can be updated to read from analysis/thailand/results/thailand_regression_output.json.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Key stats (hero strip on /thailand)
# ---------------------------------------------------------------------------

THAILAND_KEY_STATS = [
    {
        "value": "69.9%",
        "label": "of all Thai household debt comes from informal sources",
        "warn": True,
        "source": "UTCC Household Debt Survey (2024)",
    },
    {
        "value": "42.3%",
        "label": "of surveyed individuals hold an informal loan",
        "warn": True,
        "source": "PIER DP173 (Pinitjitsamut & Suwanprasert, 2022)",
    },
    {
        "value": "~220%",
        "label": "estimated annual rate charged by loan sharks (~18.3%/month)",
        "warn": True,
        "source": "PIER DP173 (2022)",
    },
    {
        "value": "฿54,300",
        "label": "average informal loan size per person",
        "warn": False,
        "source": "PIER DP173 (2022)",
    },
]

# ---------------------------------------------------------------------------
# Findings table (from published papers — labelled accordingly)
# ---------------------------------------------------------------------------

ANALYSIS_SOURCE = "published_aggregate_statistics"
ANALYSIS_NOTE = (
    "These findings are drawn from peer-reviewed publications, not original regression analysis. "
    "Primary microdata (NSO SES 2021, Townsend Thai Project) is access-gated — see the methodology note. "
    "Directional findings show which groups are associated with higher or lower formal credit access."
)

FINDINGS = [
    # --- Own analysis: Townsend Thai Project Annual Resurvey 2017 ---
    {
        "effect_label": "+0.26 pp",
        "direction": "positive",
        "group": "Per additional household member (formal loans)",
        "note": (
            "Each additional household member is associated with +0.26 percentage points higher probability "
            "of holding a formal loan (p=0.043). Larger households may face greater capital needs and "
            "have stronger social networks facilitating formal access."
        ),
        "source_key": "townsend_thai",
        "is_own_analysis": True,
        "own_analysis_note": "OLS-LPM (HC3), N=1,200 rural households. Townsend Thai 2017.",
    },
    {
        "effect_label": "+0.25 pp",
        "direction": "positive",
        "group": "Per additional household member (informal loans)",
        "note": (
            "Household size predicts informal loan holding too (+0.25 pp per member, p=0.027), "
            "suggesting that larger families borrow more across both channels — not a substitution effect."
        ),
        "source_key": "townsend_thai",
        "is_own_analysis": True,
        "own_analysis_note": "OLS-LPM (HC3), N=1,200 rural households. Townsend Thai 2017.",
    },
    {
        "effect_label": "−0.51 pp",
        "direction": "negative",
        "group": "Northeast vs Central region (informal loans)",
        "note": (
            "Northeast households are 0.51 pp less likely to hold informal loans than Central region households "
            "(p=0.007). Informal credit markets in Sisaket and Buriram appear thinner than in Chachoengsao/Lopburi — "
            "consistent with lower income and fewer lender networks."
        ),
        "source_key": "townsend_thai",
        "is_own_analysis": True,
        "own_analysis_note": "OLS-LPM (HC3), N=1,200 rural households. Townsend Thai 2017.",
    },
    # --- Published findings (JRFM 2025, national 77-province sample) ---
    {
        "effect_label": "Lower",
        "direction": "negative",
        "group": "Unemployed / freelance / retirees vs salaried",
        "note": (
            "Informal employment means no verifiable income documentation — "
            "the primary barrier to formal lender access. This group disproportionately relies on loan sharks "
            "(18%+/month). (National 77-province survey, Sep 2021.)"
        ),
        "source_key": "jrfm_2025",
        "is_own_analysis": False,
    },
    {
        "effect_label": "Higher",
        "direction": "positive",
        "group": "Farming households vs non-farming",
        "note": (
            "Farming households are more likely to hold formal credit. "
            "BAAC agricultural loan programmes and land as collateral are the likely channels. "
            "(National 77-province survey, Sep 2021.)"
        ),
        "source_key": "jrfm_2025",
        "is_own_analysis": False,
    },
    {
        "effect_label": "Higher",
        "direction": "positive",
        "group": "North / Northeast vs Bangkok (formal credit)",
        "note": (
            "North and Northeast households are more likely to access formal finance than Bangkok households "
            "in the national survey. Bangkok's deep informal market reduces the relative formal-channel advantage "
            "for lower-income residents. (National 77-province survey, Sep 2021.)"
        ),
        "source_key": "jrfm_2025",
        "is_own_analysis": False,
    },
    {
        "effect_label": "Higher rate",
        "direction": "negative",
        "group": "Stronger lender social influence (Bangkok metro)",
        "note": (
            "In the Bangkok metropolitan informal market (Bangkok, Nonthaburi, Pathum Thani, Samut Prakan), "
            "greater lender influence over the borrower is independently associated with higher informal interest rates — "
            "regardless of loan size or borrower income. (694 respondents, 2017.)"
        ),
        "source_key": "iaer_2018",
        "is_own_analysis": False,
    },
]

# ---------------------------------------------------------------------------
# Regulatory lending tiers (Thailand)
# ---------------------------------------------------------------------------

REGULATED_TIERS = [
    {
        "name": "Nano Finance",
        "max_apr_pct": 33.0,
        "loan_limit_thb": 100_000,
        "regulator": "Bank of Thailand",
        "notes": "฿100,000 limit per borrower. Max 33% APR (all-in: interest + fees + insurance).",
        "target": "Low-income individuals without formal collateral",
    },
    {
        "name": "Pico Finance",
        "max_apr_pct": 36.0,
        "loan_limit_thb": 50_000,
        "regulator": "Fiscal Policy Office (FPO), Ministry of Finance",
        "notes": "฿50,000 limit. Max 36% APR. 1,143 licensed entities in 75 provinces (Oct 2024).",
        "target": "Informal-sector workers without bank access",
    },
    {
        "name": "Pico Plus",
        "max_apr_pct": 36.0,
        "loan_limit_thb": 100_000,
        "regulator": "Fiscal Policy Office (FPO), Ministry of Finance",
        "notes": "฿100,000 limit. Max 36% APR. Higher tier for borrowers with demonstrated repayment history.",
        "target": "Established Pico borrowers with repayment track record",
    },
    {
        "name": "Village Fund / TVURF",
        "max_apr_pct": 10.0,
        "loan_limit_thb": 20_000,
        "regulator": "Village and Urban Community Fund Office",
        "notes": "Government-backed revolving fund. Rates typically 6–10% APR. Availability varies by village.",
        "target": "Village / community members in registered fund areas",
    },
]

# Representative rates for the cost comparison panel
REGULATED_COMPARISON = {
    "formal": {
        "label": "Regulated lender (Pico / Nano Finance)",
        "rate_apr_pct": 36.0,
        "rate_note": "Max 36% APR (Pico Finance cap)",
        "limit_thb": 100_000,
        "regulated_by": "Bank of Thailand / FPO",
        "recourse": "Legal framework",
        "npl_rate_note": "23.4% NPL rate (Q1 2025, FPO) — indicates high borrower stress even in formal channel",
    },
    "informal": {
        "label": "Informal lender (loan shark / in-area investor)",
        "rate_monthly_pct": 18.3,
        "rate_apr_pct": 219.6,
        "rate_note": "~18.3%/month (~220% APR) for loan sharks; 10–11%/month for in-area informal investors",
        "avg_loan_thb": 54_300,
        "regulated_by": "Unregulated",
        "recourse": "None",
    },
}

# ---------------------------------------------------------------------------
# Province list (all 77 Thai provinces, Bangkok / Khlong Toei highlighted)
# ---------------------------------------------------------------------------

FIELDWORK_PROVINCE = "Bangkok"
FIELDWORK_DISTRICT = "Khlong Toei"

THAI_PROVINCES = [
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

PROVINCE_ALIASES: dict[str, str] = {
    "bkk": "Bangkok",
    "krungthep": "Bangkok",
    "chiang mai": "Chiang Mai",
    "chiangmai": "Chiang Mai",
    "khon kaen": "Khon Kaen",
    "korat": "Nakhon Ratchasima",
    "nakorn ratchasima": "Nakhon Ratchasima",
    "nakhon ratchasima": "Nakhon Ratchasima",
    "ayutthaya": "Phra Nakhon Si Ayutthaya",
    "phuket": "Phuket",
    "pattaya": "Chon Buri",
    "chonburi": "Chon Buri",
    "nonthaburi": "Nonthaburi",
    "pathum thani": "Pathum Thani",
    "pathumthani": "Pathum Thani",
    "samut prakan": "Samut Prakan",
    "samutprakan": "Samut Prakan",
}

# ---------------------------------------------------------------------------
# Citations (stored as data, not hardcoded strings)
# ---------------------------------------------------------------------------

CITATIONS = {
    "pier_dp173": {
        "authors": "Pinitjitsamut, P., & Suwanprasert, W.",
        "year": 2022,
        "title": "Informal Loans in Thailand: Stylized Facts and Empirical Analysis",
        "journal": "PIER Discussion Paper 173",
        "url": "https://www.pier.or.th/en/dp/173/",
        "n": 4800,
        "coverage": "12 provinces, 6 Thai regions",
        "data_year": 2021,
    },
    "jrfm_2025": {
        "authors": "Srisawad, S., et al.",
        "year": 2025,
        "title": "Credit Segmentation and Household Vulnerability in Thailand: Formal Versus Informal Debt Risks",
        "journal": "Journal of Risk and Financial Management",
        "volume": "18(11):632",
        "doi": "10.3390/jrfm18110632",
        "url": "https://www.mdpi.com/1911-8074/18/11/632",
        "n": 6949,
        "coverage": "77 provinces, September 2021",
        "data_year": 2021,
    },
    "iaer_2018": {
        "authors": "Tanomchat, W., & Sampattavanija, S.",
        "year": 2018,
        "title": "Dependence of Informal Interest Rates and Level of Lenders' Influence in the Informal Loan Market in Thailand",
        "journal": "International Advances in Economic Research",
        "volume": "24(1):47–63",
        "doi": "10.1007/s11294-018-9672-1",
        "url": "https://link.springer.com/article/10.1007/s11294-018-9672-1",
        "n": 694,
        "coverage": "Bangkok, Nonthaburi, Pathum Thani, Samut Prakan",
        "data_year": 2017,
    },
    "nso_ses": {
        "authors": "National Statistical Office of Thailand",
        "year": 2021,
        "title": "Household Socio-Economic Survey 2021",
        "journal": "NSO Thailand",
        "url": "https://www.nso.go.th",
        "n": None,
        "coverage": "77 provinces, national",
        "data_year": 2021,
        "access_note": "Microdata access-gated; formal request required from NSO.",
    },
    "townsend_thai": {
        "authors": "Townsend, R. M. et al.",
        "year": 2020,
        "title": "Townsend Thai Project Annual Resurvey",
        "journal": "Harvard Dataverse",
        "url": "https://dataverse.harvard.edu/dataverse/townsend_thai",
        "n": None,
        "coverage": "Chachoengsao, Lopburi (Central); Sisaket, Buriram (Northeast)",
        "data_year": 2019,
        "access_note": "Archived on Harvard Dataverse; login may be required.",
    },
}

# ---------------------------------------------------------------------------
# Claude system prompt context for /thailand/chat
# ---------------------------------------------------------------------------

THAI_CHAT_CONTEXT = """
You are Nudge Thailand — a financial guidance chatbot helping Thai households understand loan costs
and compare informal borrowing with regulated alternatives.

Always preserve ฿ amounts, percentages, lender names, and numbered list items exactly as given in the facts.

Key facts about Thailand informal lending (from published research):
- 69.9% of total Thai household debt is from informal sources (UTCC Household Debt Survey, 2024).
- 42.3% of surveyed individuals hold an informal loan; average size ฿54,300 (PIER DP173, 2022).
- Loan sharks charge ~18.3% per month (~220% APR). In-area informal investors charge ~10–11%/month (~120–130% APR).
- Thailand's household debt-to-GDP ratio is 91.3% (Bank of Thailand, end 2023) — one of the highest in Asia.

Regulated alternatives (rates are APR caps, all-in including fees):
- Nano Finance: up to ฿100,000, max 33% APR. Regulated by Bank of Thailand.
- Pico Finance: up to ฿50,000, max 36% APR. Licensed by FPO (Ministry of Finance). 1,143 licensed entities in 75 provinces.
- Pico Plus: up to ฿100,000, max 36% APR. Higher tier for established borrowers.
- Village Fund (TVURF): typically 6–10% APR for community members in registered fund areas.

Who is most excluded from formal credit (from published research):
- Unemployed, freelance, and informal-sector workers have least access to formal credit.
- Farming households have better formal access (BAAC agricultural loans, land as collateral).
- Bangkok's informal market is deep — many lower-income urban residents use informal lenders even though formal options exist nearby.
- Lender social influence over the borrower increases the informal rate charged.

Currency: Always use ฿ (Thai Baht, THB).
When comparing, use Pico Finance (36% APR) as the regulated benchmark unless Village Fund applies.
Always note that Pico Finance NPL rate is 23.4% (Q1 2025) — even formal channels show strain.

TODO: FPO licensed Pico lender directory by province is a future scrape task, not yet available.
For now, direct users to the FPO website (fpo.go.th) to search for licensed Pico operators in their province.

When helping users, reference the research naturally. Always note source when quoting statistics.
Do not claim to approve any loan or lender.
""".strip()

# ---------------------------------------------------------------------------
# Fieldwork note (footer)
# ---------------------------------------------------------------------------

FIELDWORK_NOTE = (
    "Thailand analysis is based on peer-reviewed published statistics (PIER DP173, 2022; "
    "Srisawad et al., J. Risk Financial Manag. 18(11):632, 2025; Tanomchat & Sampattavanija, "
    "Int. Adv. Econ. Res. 24(1), 2018), pending access to NSO SES 2021 microdata. "
    "Primary fieldwork in Klong Toey (Duang Prateep Foundation, Bangkok Community Help Foundation, "
    "and others) is planned for August 2026 to extend and validate this analysis with direct survey data."
)
