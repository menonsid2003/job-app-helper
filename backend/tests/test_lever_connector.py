import httpx

from app.connectors.lever import LeverConnector
from app.schemas import CriteriaConfig

FAKE_POSTINGS = [
    {
        "text": "Senior Data Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "categories": {"location": "New York, NY"},
        "descriptionPlain": "Build data pipelines.",
        "lists": [{"text": "Requirements", "content": "<li>SQL</li><li>Python</li>"}],
        "additionalPlain": "Acme is an equal opportunity employer.",
        "createdAt": 1700000000000,
        "country": "US",
    },
    {
        "text": "Data Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/def-456",
        "categories": {"location": "London, United Kingdom"},
        "descriptionPlain": "Build data pipelines in London.",
        "lists": [],
        "additionalPlain": "",
        "createdAt": 1700000000000,
        "country": "GB",
    },
]


def _handler(request: httpx.Request) -> httpx.Response:
    assert "api.lever.co" in str(request.url)
    return httpx.Response(200, json=FAKE_POSTINGS)


def test_lever_connector_parses_postings_and_country_hint():
    transport = httpx.MockTransport(_handler)
    connector = LeverConnector(transport=transport)
    criteria = CriteriaConfig(target_companies={"lever": ["acme"]})

    listings = connector.search(criteria)

    assert len(listings) == 2
    us_job = listings[0]
    assert us_job.title == "Senior Data Engineer"
    assert us_job.company == "Acme"
    assert us_job.location == "New York, NY"
    assert us_job.country_hint == "US"
    assert "Build data pipelines." in us_job.description
    assert "SQL" in us_job.description
    assert "equal opportunity employer" in us_job.description
    assert us_job.source == "lever"

    uk_job = listings[1]
    assert uk_job.country_hint == "GB"


def test_lever_connector_skips_board_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    connector = LeverConnector(transport=transport)
    criteria = CriteriaConfig(target_companies={"lever": ["doesnotexist"]})

    listings = connector.search(criteria)

    assert listings == []


def test_lever_connector_reads_only_lever_tokens_from_target_companies():
    transport = httpx.MockTransport(_handler)
    connector = LeverConnector(transport=transport)
    criteria = CriteriaConfig(target_companies={"greenhouse": ["stripe"], "lever": ["acme"]})

    listings = connector.search(criteria)

    assert len(listings) == 2  # only polled the "lever" token list, not "greenhouse"


def test_lever_connector_returns_nothing_when_company_board_connectors_disabled():
    transport = httpx.MockTransport(_handler)
    connector = LeverConnector(transport=transport)
    criteria = CriteriaConfig(target_companies={"lever": ["acme"]}, company_board_connectors_enabled=False)

    listings = connector.search(criteria)

    assert listings == []
