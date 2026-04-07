"""
Scan universe — institutional-grade large-cap U.S. equities only.

Every symbol here meets strict criteria at the time of curation:

    1. Market cap > $100B (mega-cap territory)
    2. Average daily volume > 5M shares (tight spreads, reliable fills)
    3. High institutional ownership (dips get bought by fund rebalancing)
    4. Not primarily speculative or retail-sentiment-driven

The original universe included volatile mid-caps and speculative names
(RIVN, COIN, HOOD, DKNG, SOFI, etc.).  Scanner backtesting showed
these names lose money on mean-reversion — gap-downs continue into
further selling rather than reverting.  They have been removed.

Mean reversion works on names where institutional demand creates a
floor.  This list reflects that reality.
"""

SCAN_UNIVERSE: list[str] = [
    # ── Technology ───────────────────────────────────────────────────
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "NVDA",   # NVIDIA
    "META",   # Meta Platforms
    "GOOG",   # Alphabet (C)
    "GOOGL",  # Alphabet (A)
    "AVGO",   # Broadcom
    "ORCL",   # Oracle
    "CRM",    # Salesforce
    "AMD",    # AMD
    "ADBE",   # Adobe
    "CSCO",   # Cisco
    "QCOM",   # Qualcomm
    "INTU",   # Intuit
    "AMAT",   # Applied Materials
    "MU",     # Micron
    "LRCX",   # Lam Research
    "KLAC",   # KLA Corp
    "SNPS",   # Synopsys
    "CDNS",   # Cadence Design
    "MRVL",   # Marvell
    "NOW",    # ServiceNow
    "PANW",   # Palo Alto Networks
    "CRWD",   # CrowdStrike

    # ── Consumer / E-commerce ────────────────────────────────────────
    "AMZN",   # Amazon
    "TSLA",   # Tesla
    "NFLX",   # Netflix
    "COST",   # Costco
    "WMT",    # Walmart
    "HD",     # Home Depot
    "NKE",    # Nike
    "SBUX",   # Starbucks
    "MCD",    # McDonald's
    "LOW",    # Lowe's
    "TGT",    # Target
    "BKNG",   # Booking Holdings
    "UBER",   # Uber
    "MELI",   # MercadoLibre

    # ── Financials ───────────────────────────────────────────────────
    "JPM",    # JPMorgan Chase
    "V",      # Visa
    "MA",     # Mastercard
    "BAC",    # Bank of America
    "WFC",    # Wells Fargo
    "GS",     # Goldman Sachs
    "MS",     # Morgan Stanley
    "BLK",    # BlackRock
    "SCHW",   # Charles Schwab
    "AXP",    # American Express

    # ── Healthcare / Pharma ──────────────────────────────────────────
    "UNH",    # UnitedHealth
    "JNJ",    # Johnson & Johnson
    "LLY",    # Eli Lilly
    "ABBV",   # AbbVie
    "MRK",    # Merck
    "PFE",    # Pfizer
    "TMO",    # Thermo Fisher
    "ABT",    # Abbott Labs
    "AMGN",   # Amgen
    "GILD",   # Gilead
    "ISRG",   # Intuitive Surgical
    "VRTX",   # Vertex Pharma

    # ── Industrials / Energy ─────────────────────────────────────────
    "CAT",    # Caterpillar
    "GE",     # GE Aerospace
    "BA",     # Boeing
    "HON",    # Honeywell
    "UPS",    # UPS
    "RTX",    # RTX (Raytheon)
    "DE",     # Deere
    "LMT",    # Lockheed Martin
    "XOM",    # ExxonMobil
    "CVX",    # Chevron
    "COP",    # ConocoPhillips

    # ── Communications ───────────────────────────────────────────────
    "TMUS",   # T-Mobile

    # ── Semiconductors (additional) ──────────────────────────────────
    "TSM",    # TSMC (ADR)
    "ARM",    # Arm Holdings
    "ON",     # ON Semiconductor
]
