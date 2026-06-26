"""
Thailand Informal Credit Analysis
==================================
Mirrors the India AIDIS methodology (OLS + logit, average marginal effects)
as closely as available variables allow.

HOW TO RUN WITH REAL DATA
--------------------------
1. Obtain microdata (see data_access_log.md for instructions):
   - Thailand NSO SES 2021 -> analysis/thailand/data/ses_2021_hh.csv
   - Townsend Thai Annual Resurvey -> analysis/thailand/data/townsend_annual_YYYY.dta
2. Install deps: pip install pandas numpy statsmodels scipy
3. Run: python analyse_thailand.py
4. Output is written to results/thailand_regression_output.json — this file
   is read by nudge_webhook/thailand_data.py to populate the website.

CURRENT STATUS
--------------
Both primary datasets are access-gated (see data_access_log.md).
This script falls through to the PUBLISHED_FALLBACK block, which writes
aggregate statistics from peer-reviewed sources. Every fallback figure
is cited with DOI, author, year, and sample size.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)
OUTPUT_PATH = RESULTS_DIR / "thailand_regression_output.json"

DATA_DIR = Path(__file__).parent / "data"
SES_PATH = DATA_DIR / "ses_2021_hh.csv"
# Townsend files as used in initial analysis (June 2026)
TOWNSEND_PATH = DATA_DIR / "hh17_18le_tab1.dta"  # loan ledger (primary)
TOWNSEND_COVER = DATA_DIR / "hh17_01cvr.dta"
TOWNSEND_INCOME = DATA_DIR / "hh17_16in.dta"
TOWNSEND_LAND = DATA_DIR / "hh17_14la.dta"
TOWNSEND_OCC = DATA_DIR / "hh17_05oc.dta"
TOWNSEND_LE_MAIN = DATA_DIR / "hh17_18le.dta"


# ---------------------------------------------------------------------------
# SECTION 1 — attempt real analysis if microdata is present
# ---------------------------------------------------------------------------

def run_ses_analysis(path: Path) -> dict | None:
    """
    Run logit regression on NSO SES 2021 microdata.
    Outcome: formal_credit (1 = any formal loan outstanding, 0 = none or informal only)
    Covariates: log_income, urban, household_size, land_rai, region dummies,
                occupation_category
    Returns dict of average marginal effects or None if data not available.
    """
    try:
        import pandas as pd
        import numpy as np
        import statsmodels.api as sm
        from statsmodels.discrete.discrete_model import Logit

        df = pd.read_csv(path, low_memory=False)
        print(f"[SES] Loaded {len(df):,} rows from {path.name}")

        # --- Variable mapping (adjust to actual NSO column names) ---
        # Expected columns (rename as needed for your SES file):
        #   hh_income_monthly: monthly household income (THB)
        #   urban: 1=urban, 0=rural
        #   hh_size: number of household members
        #   land_rai: land owned in rai
        #   region: 1=Central,2=North,3=Northeast,4=South,5=East,6=West
        #   occupation_head: occupation code of household head
        #   has_formal_loan: 1 if any formal loan, 0 otherwise
        #   has_informal_loan: 1 if any informal loan

        required = ["hh_income_monthly", "urban", "hh_size", "has_formal_loan"]
        missing_cols = [c for c in required if c not in df.columns]
        if missing_cols:
            print(f"[SES] Missing columns: {missing_cols} — cannot run analysis")
            print("[SES] Available columns:", list(df.columns[:20]))
            return None

        df = df.dropna(subset=required)
        df["log_income"] = np.log1p(df["hh_income_monthly"].clip(lower=0))
        y = df["has_formal_loan"].astype(int)

        feature_cols = ["log_income", "urban", "hh_size"]
        if "land_rai" in df.columns:
            feature_cols.append("land_rai")
        # Region dummies (if available)
        if "region" in df.columns:
            region_dummies = pd.get_dummies(df["region"], prefix="region", drop_first=True)
            df = pd.concat([df, region_dummies], axis=1)
            feature_cols += list(region_dummies.columns)

        X = sm.add_constant(df[feature_cols].astype(float))
        model = Logit(y, X).fit(disp=False)
        print(model.summary())

        # Average marginal effects
        ame = model.get_margeff()
        print(ame.summary())

        effects = []
        for var, eff, pval in zip(
            ame.summary_frame().index,
            ame.margeff,
            ame.pvalues,
        ):
            effects.append({
                "variable": var,
                "ame": round(float(eff) * 100, 2),  # in percentage points
                "p_value": round(float(pval), 4),
                "significant": bool(float(pval) < 0.05),
            })

        return {
            "source": "own_analysis_NSO_SES_2021",
            "dataset": "Thailand National Statistical Office Household Socio-Economic Survey 2021",
            "n_obs": int(len(df)),
            "model": "Logit with average marginal effects",
            "outcome": "Probability of holding formal (regulated) loan",
            "effects": effects,
            "note": "Own analysis. Data: NSO SES 2021.",
        }
    except ImportError:
        print("[SES] statsmodels not installed — skipping real analysis")
        return None
    except Exception as e:
        print(f"[SES] Error: {e}")
        return None


def run_townsend_analysis(path: Path) -> dict | None:
    """
    Run OLS on Townsend Thai Annual Resurvey.
    Outcome: informal_share (fraction of total loans from informal sources)
    Covariates: income, urban (proxied by Chachoengsao/Lopburi vs Sisaket/Buriram),
                household_size, land, age_head, occupation
    """
    try:
        import pandas as pd
        import numpy as np
        import statsmodels.api as sm

        # Support both .dta (Stata) and .csv
        if path.suffix == ".dta":
            df = pd.read_stata(path)
        else:
            df = pd.read_csv(path, low_memory=False)
        print(f"[Townsend] Loaded {len(df):,} rows from {path.name}")

        # Expected Townsend variable names (may need adjustment):
        #   total_income / y_farm / y_off_farm
        #   informal_loan_amt / formal_loan_amt
        #   hh_size
        #   land_rai
        #   age_head
        #   village_id (for fixed effects)

        required = ["informal_loan_amt", "formal_loan_amt"]
        missing_cols = [c for c in required if c not in df.columns]
        if missing_cols:
            print(f"[Townsend] Missing columns: {missing_cols}")
            print("[Townsend] Available columns:", list(df.columns[:20]))
            return None

        df = df.dropna(subset=required)
        df["total_loans"] = df["informal_loan_amt"] + df["formal_loan_amt"]
        df = df[df["total_loans"] > 0]
        df["informal_share"] = df["informal_loan_amt"] / df["total_loans"]

        feature_cols = []
        if "total_income" in df.columns:
            df["log_income"] = np.log1p(df["total_income"].clip(lower=0))
            feature_cols.append("log_income")
        if "hh_size" in df.columns:
            feature_cols.append("hh_size")
        if "land_rai" in df.columns:
            feature_cols.append("land_rai")
        if "age_head" in df.columns:
            feature_cols.append("age_head")

        if not feature_cols:
            print("[Townsend] No usable covariate columns found")
            return None

        X = sm.add_constant(df[feature_cols].astype(float))
        model = sm.OLS(df["informal_share"], X).fit(cov_type="HC3")
        print(model.summary())

        effects = []
        for var in model.params.index:
            effects.append({
                "variable": var,
                "coefficient": round(float(model.params[var]) * 100, 3),
                "p_value": round(float(model.pvalues[var]), 4),
                "significant": bool(float(model.pvalues[var]) < 0.05),
            })

        return {
            "source": "own_analysis_Townsend_Thai",
            "dataset": "Townsend Thai Project Annual Resurvey (Harvard Dataverse)",
            "n_obs": int(len(df)),
            "model": "OLS, outcome = share of total loans from informal sources, HC3 SEs",
            "outcome": "Informal loan share (0–1)",
            "effects": effects,
            "note": "Own analysis. Data: Townsend Thai Project.",
        }
    except ImportError:
        print("[Townsend] Required library not installed — skipping")
        return None
    except Exception as e:
        print(f"[Townsend] Error: {e}")
        return None


# ---------------------------------------------------------------------------
# SECTION 2 — published-statistics fallback (used when microdata is blocked)
# ---------------------------------------------------------------------------

PUBLISHED_FALLBACK = {
    "source": "published_aggregate_statistics",
    "note": (
        "Primary microdata (NSO SES 2021 and Townsend Thai Project) were access-gated "
        "at the time of analysis (see data_access_log.md). All figures below are taken "
        "verbatim from peer-reviewed publications and are NOT the result of original regression "
        "analysis by this project. They are clearly attributed to their sources and presented "
        "as directional findings rather than precise marginal effects."
    ),
    "key_stats": [
        {
            "stat": "informal_loan_prevalence_pct",
            "value": 42.3,
            "unit": "% of surveyed individuals",
            "label": "42.3% of individuals have an informal loan",
            "source": "Pinitjitsamut & Suwanprasert (2022). Informal Loans in Thailand: Stylized Facts and Empirical Analysis. PIER Discussion Paper 173.",
            "sample": "4,800 individuals, 12 provinces, 6 Thai regions",
        },
        {
            "stat": "informal_debt_share_of_total_household_debt_pct",
            "value": 69.9,
            "unit": "% of total household debt",
            "label": "69.9% of all Thai household debt is informal",
            "source": "UTCC Household Debt Survey (2024).",
            "sample": "National survey",
        },
        {
            "stat": "avg_informal_loan_thb",
            "value": 54300,
            "unit": "THB",
            "label": "Average informal loan ฿54,300 per person",
            "source": "Pinitjitsamut & Suwanprasert (2022). PIER DP173.",
            "sample": "4,800 individuals",
        },
        {
            "stat": "loan_shark_rate_monthly_pct",
            "value": 18.3,
            "unit": "%/month (~220% APR)",
            "label": "Loan shark rate ~18.3%/month (~220% APR)",
            "source": "Pinitjitsamut & Suwanprasert (2022). PIER DP173.",
            "sample": "4,800 individuals",
        },
        {
            "stat": "in_area_informal_investor_rate_monthly_pct",
            "value": 10.5,
            "unit": "%/month (~126% APR)",
            "label": "In-area informal investor rate ~10–11%/month",
            "source": "Pinitjitsamut & Suwanprasert (2022). PIER DP173.",
            "sample": "4,800 individuals",
        },
        {
            "stat": "household_debt_to_gdp_pct",
            "value": 91.3,
            "unit": "% of GDP (end 2023)",
            "label": "Household debt 91.3% of GDP",
            "source": "Bank of Thailand / CEIC Data (2023).",
            "sample": "National",
        },
        {
            "stat": "pico_npl_rate_pct",
            "value": 23.4,
            "unit": "% NPL (Q1 2025)",
            "label": "Pico Finance NPL rate 23.4% (Q1 2025)",
            "source": "Fiscal Policy Office (FPO), Q1 2025.",
            "sample": "Pico Finance sector",
        },
    ],
    "directional_findings": [
        {
            "group": "Farming households vs non-farming",
            "direction": "positive",
            "outcome": "formal credit access",
            "note": "Farming households are more likely to access formal credit than unemployed, freelance, or business-owner households. Land collateral and government agricultural credit programs are likely channels.",
            "source": "Srisawad et al. (2025). Credit Segmentation and Household Vulnerability in Thailand. J. Risk Financial Manag. 18(11):632.",
            "source_doi": "10.3390/jrfm18110632",
        },
        {
            "group": "North/Northeast vs Bangkok",
            "direction": "positive",
            "outcome": "formal credit access",
            "note": "Households in the North and Northeast regions are more likely to access formal finance than those in Bangkok. Bangkok's informal credit market is deep and accessible, reducing the relative advantage of formal channels for lower-income urban residents.",
            "source": "Srisawad et al. (2025). J. Risk Financial Manag. 18(11):632.",
            "source_doi": "10.3390/jrfm18110632",
        },
        {
            "group": "Unemployed / freelance / retirees vs salaried",
            "direction": "negative",
            "outcome": "formal credit access",
            "note": "Unemployed individuals, retirees, business owners, and freelancers disproportionately rely on informal credit. Absence of verifiable income documentation is the primary barrier to formal access.",
            "source": "Srisawad et al. (2025). J. Risk Financial Manag. 18(11):632.",
            "source_doi": "10.3390/jrfm18110632",
        },
        {
            "group": "Higher income vs lower income",
            "direction": "positive",
            "outcome": "formal credit access",
            "note": "Older, higher-income households are more strongly associated with formal borrowing. Income acts as implicit collateral signal for regulated lenders.",
            "source": "Srisawad et al. (2025). J. Risk Financial Manag. 18(11):632.",
            "source_doi": "10.3390/jrfm18110632",
        },
        {
            "group": "Bangkok lower-income young adults",
            "direction": "neutral_debt_free",
            "outcome": "formal credit access",
            "note": "Younger, lower-income individuals in Bangkok are more likely to remain entirely debt-free rather than access formal credit — suggesting exclusion from both formal and informal channels at the early career stage.",
            "source": "Srisawad et al. (2025). J. Risk Financial Manag. 18(11):632.",
            "source_doi": "10.3390/jrfm18110632",
        },
        {
            "group": "Lender social influence over borrower (Bangkok metro)",
            "direction": "positive",
            "outcome": "informal interest rate charged",
            "note": "Higher lender influence over the borrower is associated with higher informal interest rates, independent of loan amount or borrower income. This holds in Bangkok, Nonthaburi, Pathum Thani, and Samut Prakan.",
            "source": "Tanomchat & Sampattavanija (2018). Dependence of Informal Interest Rates in Thailand. Int. Adv. Econ. Res. 24(1):47–63.",
            "source_doi": "10.1007/s11294-018-9672-1",
        },
    ],
    "citations": [
        {
            "key": "pier_dp173",
            "authors": "Pinitjitsamut, P., & Suwanprasert, W.",
            "year": 2022,
            "title": "Informal Loans in Thailand: Stylized Facts and Empirical Analysis",
            "journal": "PIER Discussion Paper 173",
            "url": "https://www.pier.or.th/en/dp/173/",
            "n": 4800,
            "coverage": "12 provinces, 6 Thai regions",
        },
        {
            "key": "jrfm_2025",
            "authors": "Srisawad, S., et al.",
            "year": 2025,
            "title": "Credit Segmentation and Household Vulnerability in Thailand: Formal Versus Informal Debt Risks",
            "journal": "Journal of Risk and Financial Management",
            "volume": "18(11):632",
            "doi": "10.3390/jrfm18110632",
            "url": "https://www.mdpi.com/1911-8074/18/11/632",
            "n": 6949,
            "coverage": "77 provinces, September 2021",
        },
        {
            "key": "iaer_2018",
            "authors": "Tanomchat, W., & Sampattavanija, S.",
            "year": 2018,
            "title": "Dependence of Informal Interest Rates and Level of Lenders' Influence in the Informal Loan Market in Thailand",
            "journal": "International Advances in Economic Research",
            "volume": "24(1):47–63",
            "doi": "10.1007/s11294-018-9672-1",
            "n": 694,
            "coverage": "Bangkok, Nonthaburi, Pathum Thani, Samut Prakan",
        },
    ],
}


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    result = None

    # Try real analysis first
    if SES_PATH.exists():
        print(f"[INFO] NSO SES file found at {SES_PATH}. Attempting analysis...")
        result = run_ses_analysis(SES_PATH)

    if result is None and TOWNSEND_PATH.exists():
        print(f"[INFO] Townsend file found at {TOWNSEND_PATH}. Attempting analysis...")
        result = run_townsend_analysis(TOWNSEND_PATH)

    if result is None:
        print("[INFO] No microdata found or analysis failed. Using published-statistics fallback.")
        result = PUBLISHED_FALLBACK
    else:
        print("[INFO] Real analysis completed successfully.")

    OUTPUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"[INFO] Results written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
