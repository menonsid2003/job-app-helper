# Display-name lookups for connectors whose APIs don't return a human-
# readable company name alongside each posting (Greenhouse and Lever both
# key everything off the URL slug/token). Falls back to a title-cased token
# for anything not listed here — see each connector's _fetch_company.

GREENHOUSE_COMPANY_DISPLAY_NAMES: dict[str, str] = {
    "stripe": "Stripe",
    "figma": "Figma",
    "asana": "Asana",
    "robinhood": "Robinhood",
    "gitlab": "GitLab",
    "coinbase": "Coinbase",
    "discord": "Discord",
    "cloudflare": "Cloudflare",
    "elastic": "Elastic",
    "databricks": "Databricks",
    "brex": "Brex",
    "affirm": "Affirm",
    "airtable": "Airtable",
    "mongodb": "MongoDB",
    "instacart": "Instacart",
    "lyft": "Lyft",
    "pinterest": "Pinterest",
    "samsara": "Samsara",
}

LEVER_COMPANY_DISPLAY_NAMES: dict[str, str] = {
    "palantir": "Palantir",
    "plaid": "Plaid",
    "lever": "Lever",
    "clari": "Clari",
    "spotify": "Spotify",
    "kraken": "Kraken",
    "immutable": "Immutable",
    "anchorage": "Anchorage Digital",
    "ledger": "Ledger",
}

WORKDAY_COMPANY_DISPLAY_NAMES: dict[str, str] = {
    "redhat": "Red Hat",
    "qualys": "Qualys",
    "zebra": "Zebra Technologies",
    "tempus": "Tempus",
    "alteryx": "Alteryx",
    "workday": "Workday",
}
