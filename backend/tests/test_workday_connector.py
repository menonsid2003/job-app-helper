import json

import httpx
import pytest

from app.connectors.workday import WorkdayConnector, _parse_workday_site_url
from app.schemas import CriteriaConfig


def test_parse_workday_site_url_basic():
    site = _parse_workday_site_url("https://redhat.wd5.myworkdayjobs.com/Jobs")
    assert site.tenant == "redhat"
    assert site.cxs_base == "https://redhat.wd5.myworkdayjobs.com/wday/cxs/redhat/Jobs"
    assert site.browse_base == "https://redhat.wd5.myworkdayjobs.com/Jobs"


def test_parse_workday_site_url_discards_locale_prefix_for_cxs_but_keeps_it_for_browse():
    site = _parse_workday_site_url("https://qualys.wd5.myworkdayjobs.com/en-US/Careers")
    assert site.tenant == "qualys"
    assert site.cxs_base == "https://qualys.wd5.myworkdayjobs.com/wday/cxs/qualys/Careers"
    assert site.browse_base == "https://qualys.wd5.myworkdayjobs.com/en-US/Careers"


def test_parse_workday_site_url_rejects_malformed_url():
    with pytest.raises(ValueError):
        _parse_workday_site_url("not-a-url")
    with pytest.raises(ValueError):
        _parse_workday_site_url("https://example.com")  # no site-name path segment


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/jobs") and request.method == "POST":
        body = json.loads(request.content)
        if body["offset"] == 0:
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "jobPostings": [
                        {
                            "title": "Software Engineer",
                            "externalPath": "/job/Remote/Software-Engineer_R1",
                            "locationsText": "Remote",
                            "postedOn": "Posted Today",
                        },
                        {
                            "title": "Marketing Manager",
                            "externalPath": "/job/Remote/Marketing-Manager_R2",
                            "locationsText": "Remote",
                            "postedOn": "Posted Today",
                        },
                    ],
                },
            )
        return httpx.Response(200, json={"total": 2, "jobPostings": []})
    if "/job/Remote/Software-Engineer_R1" in path:
        return httpx.Response(
            200, json={"jobPostingInfo": {"jobDescription": "<p>Build things &amp; stuff.</p>"}}
        )
    return httpx.Response(404)


def test_workday_connector_filters_by_title_before_fetching_detail():
    transport = httpx.MockTransport(_handler)
    connector = WorkdayConnector(transport=transport)
    criteria = CriteriaConfig(
        target_roles=["Software Engineer"],
        target_companies={"workday": ["https://acme.wd5.myworkdayjobs.com/Careers"]},
    )

    listings = connector.search(criteria)

    # Marketing Manager was in the search results but never fetched (no detail
    # handler for it — the test would fail with a 404 mismatch if it tried).
    assert len(listings) == 1
    listing = listings[0]
    assert listing.title == "Software Engineer"
    assert listing.company == "Acme"
    assert listing.location == "Remote"
    assert listing.description == "Build things & stuff."
    assert listing.source_url == "https://acme.wd5.myworkdayjobs.com/Careers/job/Remote/Software-Engineer_R1"
    assert listing.source == "workday"


def test_workday_connector_returns_nothing_when_company_board_connectors_disabled():
    transport = httpx.MockTransport(_handler)
    connector = WorkdayConnector(transport=transport)
    criteria = CriteriaConfig(
        target_roles=["Software Engineer"],
        target_companies={"workday": ["https://acme.wd5.myworkdayjobs.com/Careers"]},
        company_board_connectors_enabled=False,
    )

    listings = connector.search(criteria)

    assert listings == []


def test_workday_connector_skips_malformed_site_url():
    transport = httpx.MockTransport(_handler)
    connector = WorkdayConnector(transport=transport)
    criteria = CriteriaConfig(
        target_roles=["Software Engineer"],
        target_companies={"workday": ["not-a-url"]},
    )

    listings = connector.search(criteria)

    assert listings == []


def test_workday_connector_continues_after_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    transport = httpx.MockTransport(handler)
    connector = WorkdayConnector(transport=transport)
    criteria = CriteriaConfig(
        target_roles=["Software Engineer"],
        target_companies={"workday": ["https://acme.wd5.myworkdayjobs.com/Careers"]},
    )

    listings = connector.search(criteria)

    assert listings == []
