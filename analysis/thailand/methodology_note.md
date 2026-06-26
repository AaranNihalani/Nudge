# Thailand Analysis — Methodology Note

**Project:** Nudge Microfinance — Thailand Case Study
**Author:** Aaran Nihalani, Eton College
**Date:** June 2026
**Planned fieldwork:** Klong Toey, Bangkok (Duang Prateep Foundation, Bangkok Community Help Foundation, and others) — August 2026

---

## Intended Methodology

The intended analysis mirrors the India AIDIS approach:

- **Model 1 (Logit):** Binary outcome — does the household hold any formal (regulated) loan? Average marginal effects by income, urban/rural, occupation type, region/province, household size, and land ownership.
- **Model 2 (OLS):** Continuous outcome — informal loan share of total household debt. Coefficients interpreted as percentage-point change in informal reliance per unit change in predictor.
- **Identification:** Province or village fixed effects where sample allows.
- **Standard errors:** Heteroskedasticity-robust (HC3) for OLS; cluster-robust at province level if N permits for logit.

The variables available in Thai datasets (income, urban, household size, land, region, occupation) are close but not identical to the Indian AIDIS variables (caste, religion, MPCE, land acres). Caste and religion are not collected in Thai government surveys; occupation category and province-level region serve as the primary group identifiers.

---

## Data Sources Attempted

### 1. Thailand NSO Household Socio-Economic Survey (SES) 2021

The 2021 SES round (even year → full income/expenditure/debt module) is the ideal primary dataset. It covers all 77 provinces with provincial stratification matching the AIDIS national design. Variables of interest: total household debt by source (formal institution vs informal lender), interest rate paid, loan purpose, household income, household size, land ownership, urban/rural classification, occupation of head.

**Access status:** Blocked. NSO controls access; formal request required via nso.go.th. See `data_access_log.md`.

### 2. Townsend Thai Project Annual Resurvey (Harvard Dataverse)

The Townsend panel (rural Chachoengsao, Lopburi, Sisaket, and Buriram) has rich loan-source variables: `informal_loan_amt`, `formal_loan_amt`, lender type, interest rate, and household balance-sheet items. Annual resurveys 2005–2019 are archived on Harvard Dataverse. Panel structure allows household fixed effects.

**Access status:** Blocked (Dataverse pages failed to render; login may be required). See `data_access_log.md`.

---

## Variables Included (when data is available)

| Variable | Source | Notes |
|----------|--------|-------|
| `has_formal_loan` | NSO SES | Binary; 1 if any regulated institution loan outstanding |
| `informal_share` | Townsend | Fraction of total loans from informal sources |
| `log_income` | Both | Log of monthly household income (THB) |
| `urban` | NSO SES | 1=urban municipal area, 0=rural |
| `hh_size` | Both | Number of household members |
| `land_rai` | Both | Land owned in rai (1 rai ≈ 0.4 acres) |
| `region` | NSO SES | 1=Central, 2=North, 3=Northeast, 4=South |
| `age_head` | Townsend | Age of household head |
| `occupation` | Both | Categorical: farming, salaried, self-employed, unemployed |

---

## What Could Not Be Controlled For

- **Caste/ethnicity:** Not collected in Thai government surveys (unlike Indian AIDIS).
- **Religion:** Not collected (unlike AIDIS).
- **Province-level lender density:** Would require a separate administrative dataset on licensed MFI branch counts by province; not currently available.
- **Loan purpose:** NSO SES records purpose codes, but these were not exploitable without the microdata.
- **Interest rate paid on informal loans:** Reported in Townsend data for some years, but not consistently; the PIER DP173 stylized facts are used as the published benchmark.

---

## Fallback: Published Aggregate Statistics

Since neither primary dataset was accessible, all statistics currently displayed on `/thailand` are sourced from peer-reviewed publications (see `data_access_log.md` for full citation list). Key sources:

1. **PIER DP173 (2022)** — 4,800 individuals, 12 provinces: informal loan prevalence (42.3%), average loan size (฿54,300), loan-shark rate (~18.3%/month).
2. **JRFM 18(11):632 (2025)** — 6,949 respondents, 77 provinces: multinomial regression showing occupation, age, income, and region as determinants of formal vs informal credit segment.
3. **Tanomchat & Sampattavanija (2018)** — 694 respondents, Bangkok metro: lender influence as determinant of informal rate.

All fallback statistics are labelled in `thailand_data.py` with `source: "published_aggregate_statistics"` so the data layer can distinguish them from original-analysis outputs.

---

## Path to Upgrading This Analysis

1. Obtain NSO SES 2021 microdata (formal request to NSO Thailand).
2. Download Townsend Thai Annual Resurvey from Harvard Dataverse (free account).
3. Place files in `analysis/thailand/data/`.
4. Run `python analysis/thailand/analyse_thailand.py` — output overwrites `results/thailand_regression_output.json`.
5. Re-deploy. The website reads from `thailand_data.py` which in turn reads from the JSON output.

Additionally, primary fieldwork at Klong Toey (Duang Prateep Foundation, Bangkok Community Help Foundation, August 2026) will generate a survey dataset with direct measures of informal interest rates, lender type, and loan purpose for Khlong Toei district — a more granular complement to the province-level NSO analysis.
