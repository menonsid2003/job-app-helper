import logging

import httpx

from app.connectors.base import Connector, ProgressCallback
from app.html_utils import strip_html
from app.schemas import CriteriaConfig, JobListing
from app.seed_companies import GREENHOUSE_COMPANY_DISPLAY_NAMES

logger = logging.getLogger(__name__)

BOARD_API_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseConnector(Connector):
    name = "greenhouse"

    def __init__(self, timeout_seconds: float = 20.0, transport: httpx.BaseTransport | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def search(self, criteria: CriteriaConfig, on_progress: ProgressCallback | None = None) -> list[JobListing]:
        if not criteria.company_board_connectors_enabled:
            return []

        listings: list[JobListing] = []
        tokens = criteria.target_companies.get("greenhouse", [])
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            for token in tokens:
                if on_progress:
                    on_progress(f"greenhouse: fetching {token}…")
                try:
                    company_listings = self._fetch_company(client, token)
                    listings.extend(company_listings)
                    if on_progress:
                        on_progress(f"greenhouse: {token} → {len(company_listings)} jobs")
                except httpx.HTTPError as exc:
                    logger.warning("Greenhouse fetch failed for board '%s': %s", token, exc)
                    if on_progress:
                        on_progress(f"greenhouse: {token} failed ({exc})")
        return listings

    def _fetch_company(self, client: httpx.Client, token: str) -> list[JobListing]:
        response = client.get(BOARD_API_URL.format(token=token), params={"content": "true"})
        response.raise_for_status()
        data = response.json()

        company_name = GREENHOUSE_COMPANY_DISPLAY_NAMES.get(token, token.replace("-", " ").title())
        listings = []
        for job in data.get("jobs", []):
            location = (job.get("location") or {}).get("name", "")
            listings.append(
                JobListing(
                    source="greenhouse",
                    source_url=job.get("absolute_url", ""),
                    title=job.get("title", ""),
                    company=company_name,
                    location=location,
                    salary_text=None,
                    description=strip_html(job.get("content", "")),
                    posted_date=job.get("first_published") or job.get("updated_at"),
                )
            )
        return listings
