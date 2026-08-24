import json

import httpx

from app.connectors.greenhouse import GreenhouseConnector
from app.schemas import CriteriaConfig

FAKE_RESPONSE = {
    "jobs": [
        {
            "title": "Full Stack Developer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
            "location": {"name": "Tampa, FL"},
            "content": "<p>Build <b>great</b> things.</p>",
            "first_published": "2026-08-01T00:00:00Z",
        },
        {
            "title": "Senior Site Reliability Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/456",
            "location": {"name": "Remote"},
            "content": "<p>Keep things running.</p>",
            "updated_at": "2026-08-05T00:00:00Z",
        },
    ]
}


def _handler(request: httpx.Request) -> httpx.Response:
    assert "boards-api.greenhouse.io" in str(request.url)
    return httpx.Response(200, json=FAKE_RESPONSE)


def test_greenhouse_connector_parses_jobs_and_strips_html():
    transport = httpx.MockTransport(_handler)
    connector = GreenhouseConnector(transport=transport)
    criteria = CriteriaConfig(target_companies={"greenhouse": ["acme"]})

    listings = connector.search(criteria)

    assert len(listings) == 2
    first = listings[0]
    assert first.title == "Full Stack Developer"
    assert first.company == "Acme"
    assert first.location == "Tampa, FL"
    assert first.description == "Build great things."
    assert first.posted_date == "2026-08-01T00:00:00Z"
    assert first.source == "greenhouse"


def test_greenhouse_connector_skips_board_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    connector = GreenhouseConnector(transport=transport)
    criteria = CriteriaConfig(target_companies={"greenhouse": ["doesnotexist"]})

    listings = connector.search(criteria)

    assert listings == []


def test_greenhouse_connector_returns_nothing_when_company_board_connectors_disabled():
    transport = httpx.MockTransport(_handler)
    connector = GreenhouseConnector(transport=transport)
    criteria = CriteriaConfig(
        target_companies={"greenhouse": ["acme"]}, company_board_connectors_enabled=False
    )

    listings = connector.search(criteria)

    assert listings == []
