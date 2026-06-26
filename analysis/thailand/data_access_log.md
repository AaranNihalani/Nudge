# Thailand Data Access Log

Attempted: 2026-06-26

## 1. Thailand NSO Household Socio-Economic Survey (SES)

**Attempted sources:**
- IHSN catalog: catalog.ihsn.org (search for Thailand SES)
- NSO portal: nso.go.th

**Outcome: BLOCKED**

The IHSN catalog entry for Thailand SES 2000 (catalog ID 1490) was located. Access to microdata is controlled by the National Statistical Office of Thailand. The "Get Microdata" link requires submitting a formal request to the NSO — anonymous public download is not available. The 2021 and 2023 rounds (the most relevant, as even-numbered years include the debt/liabilities module) were not found as publicly downloadable files in the IHSN catalog at the time of this access attempt.

**What this means:** NSO SES microdata cannot be included in a reproducible open-source analysis without user registration and NSO approval. Aggregate statistics from secondary sources and published papers are used as fallback (see below).

**To unblock:** Contact the NSO Data Service Centre at nso.go.th and request the 2021 SES microdata (the full income/expenditure/debt round). Once received, place the file at `analysis/thailand/data/ses_2021_hh.csv` (or the native NSO format) and re-run `analyse_thailand.py`.

---

## 2. Townsend Thai Project — Harvard Dataverse

**Attempted source:**
- dataverse.harvard.edu/dataverse/townsend_thai_data
- Direct dataset URL: doi:10.7910/DVN/UW4VKE (Annual Resurvey 2017 Rural)

**Outcome: BLOCKED (page rendering failure)**

The Harvard Dataverse pages for the Townsend Thai Project did not render content via automated fetch. The Monthly Survey dataset (hdl:1902.1/14795) is listed in search results with `fileAccess=Public` in the URL, suggesting some files may be publicly accessible, but the file listing and download links could not be confirmed without interactive browser access.

**What this means:** Cannot confirm which specific files (with loan-source variables) are freely downloadable vs. require Dataverse account registration. Treat as blocked pending manual verification.

**To unblock:** Visit https://dataverse.harvard.edu/dataverse/townsend_thai and log in (or create a free Dataverse account). Download the Annual Resurvey household files — look for variables named `loan_source`, `informal_loan`, `formal_credit`, `credit_source`. Place files at `analysis/thailand/data/townsend_annual_YYYY.dta` and re-run `analyse_thailand.py`.

---

## 3. Published Sources Used as Fallback

All statistics in `thailand_data.py` and displayed on `/thailand` are sourced from the following peer-reviewed or institutional publications. They are explicitly labeled "from [source], not original analysis" in the data file.

| Source | Year | Sample | Key data |
|--------|------|--------|----------|
| Pinitjitsamut & Suwanprasert, PIER DP173 | 2022 | 4,800 individuals, 12 provinces, 6 regions | 42.3% have informal loan; avg loan ฿54,300; loan shark rate ~18.3%/month (~220% APR) |
| Srisawad et al., J. Risk Financial Manag. 18(11):632, MDPI | 2025 | 6,949 respondents, 77 provinces, Sept 2021 | Occupation, age, income, and region as determinants of formal vs informal credit access |
| Tanomchat & Sampattavanija, Int. Adv. Econ. Res. 24(1):47–63 | 2018 | 694 respondents, Bangkok/Nonthaburi/Pathum Thani/Samut Prakan | Lender influence and borrower characteristics as determinants of informal rate |
| UTCC Household Debt Survey | 2024 | National survey | 69.9% of total household debt is informal |
| Bank of Thailand / CEIC | 2023 | National | Household debt-to-GDP 91.3% end 2023; peak 95.5% Q1 2021 |
| Fiscal Policy Office (FPO) | Q1 2025 | Pico sector | Pico Finance NPL rate 23.4% |
| Pattaya Mail / Bangkok Post | 2024 | Regulatory | 1,143 Pico entities in 75 provinces; ฿44.7B outstanding; 4.6M accounts |
