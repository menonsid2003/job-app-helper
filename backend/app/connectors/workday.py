import logging
from urllib.parse import urlparse

import httpx

from app.connectors.base import Connector, ProgressCallback
from app.html_utils import strip_html
from app.schemas import CriteriaConfig, JobListing
from app.scoring.prefilter import title_matches_target_role
from app.seed_companies import WORKDAY_COMPANY_DISPLAY_NAMES

logger = logging.getLogger(__name__)

# Workday's search+detail split means a naive "fetch everything" would mean
# thousands of HTTP calls for a large employer (NVIDIA alone has 2000+ open
# postings) — cap how many listing records we page through per company.
MAX_RECORDS_PER_COMPANY = 300
PAGE_SIZE = 20  # Workday's CXS API rejects (400) any limit above 20


class WorkdaySite:
    __slots__ = ("tenant", "cxs_base", "browse_base")

    def __init__(self, tenant: str, cxs_base: str, browse_base: str) -> None:
        self.tenant = tenant
        self.cxs_base = cxs_base  # e.g. https://qualys.wd5.myworkdayjobs.com/wday/cxs/qualys/Careers
        self.browse_base = browse_base  # e.g. https://qualys.wd5.myworkdayjobs.com/en-US/Careers


class WorkdayConnector(Connector):
    name = "workday"

    def __init__(self, timeout_seconds: float = 20.0, transport: httpx.BaseTransport | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def search(self, criteria: CriteriaConfig, on_progress: ProgressCallback | None = None) -> list[JobListing]:
        if not criteria.company_board_connectors_enabled:
            return []

        listings: list[JobListing] = []
        site_urls = criteria.target_companies.get("workday", [])
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            for site_url in site_urls:
                try:
                    site = _parse_workday_site_url(site_url)
                except ValueError as exc:
                    logger.warning("Skipping malformed Workday site URL '%s': %s", site_url, exc)
                    if on_progress:
                        on_progress(f"workday: skipping malformed URL '{site_url}'")
                    continue

                if on_progress:
                    on_progress(f"workday: fetching {site.tenant}…")
                try:
                    company_listings = self._fetch_company(client, site, criteria.target_roles)
                    listings.extend(company_listings)
                    if on_progress:
                        on_progress(f"workday: {site.tenant} → {len(company_listings)} title-relevant jobs")
                except httpx.HTTPError as exc:
                    logger.warning("Workday fetch failed for '%s': %s", site.tenant, exc)
                    if on_progress:
                        on_progress(f"workday: {site.tenant} failed ({exc})")
        return listings

    def _fetch_company(
        self, client: httpx.Client, site: WorkdaySite, target_roles: list[str]
    ) -> list[JobListing]:
        postings = []
        offset = 0
        total = None
        while offset < MAX_RECORDS_PER_COMPANY and (total is None or offset < total):
            response = client.post(
                f"{site.cxs_base}/jobs",
                json={"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""},
            )
            response.raise_for_status()
            data = response.json()
            total = data.get("total", 0)
            page = data.get("jobPostings", [])
            if not page:
                break
            postings.extend(page)
            offset += PAGE_SIZE

        # Filter by title before spending a detail HTTP call — Workday's
        # search and detail endpoints are separate, unlike Greenhouse/Lever's
        # single bulk-content call, so this matters a lot for large employers.
        relevant = [p for p in postings if title_matches_target_role(p.get("title", ""), target_roles)]

        listings = []
        for posting in relevant:
            external_path = posting.get("externalPath", "")
            if not external_path:
                continue
            try:
                detail_response = client.get(f"{site.cxs_base}{external_path}")
                detail_response.raise_for_status()
                detail = detail_response.json().get("jobPostingInfo", {})
            except httpx.HTTPError as exc:
                logger.warning("Workday detail fetch failed for %s%s: %s", site.cxs_base, external_path, exc)
                continue

            listings.append(
                JobListing(
                    source="workday",
                    source_url=f"{site.browse_base}{external_path}",
                    title=posting.get("title", ""),
                    company=WORKDAY_COMPANY_DISPLAY_NAMES.get(site.tenant, site.tenant.replace("-", " ").title()),
                    location=posting.get("locationsText", ""),
                    salary_text=None,
                    description=strip_html(detail.get("jobDescription", "")),
                    posted_date=posting.get("postedOn"),
                )
            )
        return listings


def _parse_workday_site_url(site_url: str) -> WorkdaySite:
    """Given a career site URL like
    "https://redhat.wd5.myworkdayjobs.com/Jobs" or
    "https://qualys.wd5.myworkdayjobs.com/en-US/Careers", derive the CXS API
    base (site name only, locale prefix discarded — that's how Workday's own
    API is structured) and keep the original path as the browse base for
    constructing human-facing job links."""
    parsed = urlparse(site_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("not an absolute URL")
    tenant = parsed.netloc.split(".")[0]
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        raise ValueError("URL has no site-name path segment")
    site_name = segments[-1]
    origin = f"{parsed.scheme}://{parsed.netloc}"
    cxs_base = f"{origin}/wday/cxs/{tenant}/{site_name}"
    browse_base = f"{origin}/{'/'.join(segments)}"
    return WorkdaySite(tenant=tenant, cxs_base=cxs_base, browse_base=browse_base)
