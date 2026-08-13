"""What each sector agent is told to look at — the thing that makes it *specialise*.

A single line per sector, injected into the sector agent's system prompt so a Financials
agent reasons about margins and asset quality while an Energy agent reasons about crude and
refining. Keyed by the exact taxonomy strings in `app/core/sectors.py` (Zerodha's for
India, GICS for the US); anything unmapped falls back to a generic lens. It costs nothing —
no extra call, just a better prompt.
"""

from __future__ import annotations

SECTOR_LENS: dict[str, str] = {
    # --- India (Zerodha taxonomy) ---
    "Financial services": "net interest margins, asset quality / NPAs, credit growth, deposit trends, and rate-cycle sensitivity",
    "NBFC": "cost of funds, borrowing mix, asset quality, and regulatory tightening",
    "IT": "deal wins / TCV, discretionary tech spend, operating margins, USD/INR, and attrition",
    "Software services": "recurring revenue and retention, deal pipeline, margins, and USD/INR",
    "Energy": "crude and commodity prices, refining and marketing margins, and subsidy / regulation risk",
    "Healthcare": "USFDA actions, US generics pricing pressure, and the R&D / approval pipeline",
    "Automobile": "monthly volumes, input-cost (steel) trends, the EV transition, and demand cyclicality",
    "Auto ancillary": "OEM order books, input costs, and EV content per vehicle",
    "FMCG": "rural vs urban demand, input-cost inflation, and volume- vs price-led growth",
    "Metals": "global commodity prices, China demand, input costs, and capacity utilisation",
    "Building materials": "the housing / infrastructure cycle, cement realisations, and energy input costs",
    "Real estate": "pre-sales momentum, unsold inventory, the launch pipeline, and rate sensitivity",
    "Telecom": "ARPU, subscriber additions, tariff moves, and 5G / capex intensity",
    "Chemicals": "specialty vs commodity mix, raw-material spreads, and export demand",
    "Fertilizers": "subsidy policy, raw-material (gas) costs, and monsoon-driven demand",
    "Consumer durables": "discretionary demand, input costs, and premiumisation trends",
    "Defence": "order-book and execution, indigenisation policy, and government capex",
    "Aviation": "load factors, fuel (ATF) costs, yields, and capacity",
    "Media & entertainment": "advertising spend, subscription trends, and content costs",
    "Retail": "same-store sales, footprint expansion, and margin mix",
    "Engineering & capital goods": "order inflows, execution / working capital, and the capex cycle",
    "Logistics": "freight volumes, fuel costs, and network utilisation",
    "Textiles": "cotton / input costs, export demand, and currency",
    "Diversified": "the mix of underlying businesses and where the cycle is helping or hurting",
    # --- US (GICS) ---
    "Information Technology": "guidance, operating margins, the AI / capex cycle, and enterprise demand",
    "Financials": "net interest income, credit costs, rate sensitivity, and capital-markets activity",
    "Health Care": "FDA / clinical readouts, drug-pricing policy, and utilisation trends",
    "Consumer Discretionary": "consumer spending strength, inventory levels, and discretionary demand",
    "Consumer Staples": "pricing power, input-cost inflation, and volume trends",
    "Communication Services": "advertising demand, subscriber / engagement trends, and content spend",
    "Industrials": "order backlogs, the capex cycle, and input / freight costs",
    "Utilities": "rate-base growth, interest-rate sensitivity, and regulatory approvals",
    "Materials": "commodity prices, global demand, and input costs",
}

GENERIC_LENS = (
    "recent company-specific news, valuation, profitability, and momentum versus the "
    "52-week range"
)


def lens_for(sector: str) -> str:
    return SECTOR_LENS.get(sector, GENERIC_LENS)
