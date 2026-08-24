import httpx

from app.canonical_link import resolve_canonical_link, resolve_canonical_posting, slugify_candidates


def test_slugify_candidates_basic():
    assert slugify_candidates("Stripe") == ["stripe"]


def test_slugify_candidates_multi_word_gives_hyphenated_and_joined():
    assert slugify_candidates("Red Hat") == ["red-hat", "redhat"]


def test_slugify_candidates_strips_company_suffix():
    assert slugify_candidates("Acme, Inc.") == ["acme"]


def test_slugify_candidates_empty_for_blank_input():
    assert slugify_candidates("") == []


GREENHOUSE_JOBS = {
    "jobs": [
        {
            "title": "Senior Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
            "content": "<p>Build <b>data</b> pipelines at scale.</p>",
        },
        {"title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "content": "<p>Run campaigns.</p>"},
    ]
}

LEVER_POSTINGS = [
    {
        "text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc",
        "descriptionPlain": "Own the data platform.",
        "lists": [{"text": "Requirements", "content": "<ul><li>Python</li></ul>"}],
        "additionalPlain": "Remote-friendly.",
    },
]


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "boards-api.greenhouse.io/v1/boards/acme" in url:
        return httpx.Response(200, json=GREENHOUSE_JOBS)
    if "boards-api.greenhouse.io" in url:
        return httpx.Response(404)
    if "api.lever.co/v0/postings/acme" in url:
        return httpx.Response(200, json=LEVER_POSTINGS)
    if "api.lever.co" in url:
        return httpx.Response(404)
    return httpx.Response(404)


def test_resolve_canonical_link_finds_confident_greenhouse_match():
    transport = httpx.MockTransport(_handler)
    result = resolve_canonical_link("Acme", "Data Engineer", transport=transport)
    assert result == "https://boards.greenhouse.io/acme/jobs/1"


def test_resolve_canonical_link_returns_none_for_no_good_match():
    transport = httpx.MockTransport(_handler)
    result = resolve_canonical_link("Acme", "Completely Unrelated Sales Role Title", transport=transport)
    assert result is None


def test_resolve_canonical_link_returns_none_when_company_not_found_anywhere():
    transport = httpx.MockTransport(_handler)
    result = resolve_canonical_link("Nonexistent Company Xyz", "Data Engineer", transport=transport)
    assert result is None


def test_resolve_canonical_link_falls_back_to_lever_when_greenhouse_has_no_board():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "boards-api.greenhouse.io" in url:
            return httpx.Response(404)
        if "api.lever.co/v0/postings/acme" in url:
            return httpx.Response(200, json=LEVER_POSTINGS)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    result = resolve_canonical_link("Acme", "Data Engineer", transport=transport)
    assert result == "https://jobs.lever.co/acme/abc"


def test_resolve_canonical_link_never_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    transport = httpx.MockTransport(handler)
    result = resolve_canonical_link("Acme", "Data Engineer", transport=transport)
    assert result is None


def test_resolve_canonical_posting_includes_greenhouse_content():
    transport = httpx.MockTransport(_handler)
    result = resolve_canonical_posting("Acme", "Data Engineer", transport=transport)
    assert result is not None
    assert result.url == "https://boards.greenhouse.io/acme/jobs/1"
    assert result.description == "Build data pipelines at scale."


def test_resolve_canonical_posting_assembles_lever_description_parts():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "boards-api.greenhouse.io" in url:
            return httpx.Response(404)
        if "api.lever.co/v0/postings/acme" in url:
            return httpx.Response(200, json=LEVER_POSTINGS)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    result = resolve_canonical_posting("Acme", "Data Engineer", transport=transport)
    assert result is not None
    assert result.url == "https://jobs.lever.co/acme/abc"
    assert "Own the data platform." in result.description
    assert "Python" in result.description
    assert "Remote-friendly." in result.description


def test_resolve_canonical_posting_returns_none_for_no_good_match():
    transport = httpx.MockTransport(_handler)
    result = resolve_canonical_posting("Acme", "Completely Unrelated Sales Role Title", transport=transport)
    assert result is None
