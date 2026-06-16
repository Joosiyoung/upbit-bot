# NASDAQ/NYSE 우량주 유니버스
# (symbol, name, excd) — excd: "NAS"=NASDAQ, "NYS"=NYSE

_US_UNIVERSE = [
    ("AAPL",  "Apple",           "NAS"),
    ("MSFT",  "Microsoft",       "NAS"),
    ("GOOGL", "Alphabet",        "NAS"),
    ("AMZN",  "Amazon",          "NAS"),
    ("NVDA",  "NVIDIA",          "NAS"),
    ("META",  "Meta",            "NAS"),
    ("TSLA",  "Tesla",           "NAS"),
    ("AMD",   "AMD",             "NAS"),
    ("NFLX",  "Netflix",         "NAS"),
    ("INTC",  "Intel",           "NAS"),
    ("AVGO",  "Broadcom",        "NAS"),
    ("QCOM",  "Qualcomm",        "NAS"),
    ("ADBE",  "Adobe",           "NAS"),
    ("COST",  "Costco",          "NAS"),
    ("PYPL",  "PayPal",          "NAS"),
    ("JPM",   "JPMorgan",        "NYS"),
    ("BAC",   "Bank of America", "NYS"),
    ("WMT",   "Walmart",         "NYS"),
    ("V",     "Visa",            "NYS"),
    ("MA",    "Mastercard",      "NYS"),
]


def get_us_universe() -> list[tuple[str, str, str]]:
    """(symbol, name, excd) 리스트 반환."""
    return list(_US_UNIVERSE)
