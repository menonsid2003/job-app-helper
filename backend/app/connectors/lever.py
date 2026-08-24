import logging
from datetime import datetime, timezone

import httpx

from app.connectors.base import Connector, ProgressCallback
from app.html_utils import strip_html
from app.schemas import CriteriaConfig, JobListing
from app.seed_companies import LEVER_COMPANY_DISPLAY_NAMES

logger = logging.getLogger(__name__)

POSTINGS_API_URL = "https://api.lever.co/v0/postings/{token}"


class LeverConnector(Connector):
    name = "lever"

    def __init__(self, timeout_seconds: float = 20.0, transport: httpx.BaseTransport | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def search(self, criteria: CriteriaConfig, on_progress: ProgressCallback | None = None) -> list[JobListing]:
        if not criteria.company_board_connectors_enabled:
            return []

        listings: list[JobListing] = []
        tokens = criteria.target_companies.get("lever", [])
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            for token in tokens:
                if on_progress:
                    on_progress(f"lever: fetching {token}…")
                try:
                    company_listings = self._fetch_company(client, token)
                    listings.extend(company_listings)
                    if on_progress:
                        on_progress(f"lever: {token} → {len(company_listings)} jobs")
                except httpx.HTTPError as exc:
                    logger.warning("Lever fetch failed for board '%s': %s", token, exc)
                    if on_progress:
                        on_progress(f"lever: {token} failed ({exc})")
        return listings

    def _fetch_company(self, client: httpx.Client, token: str) -> list[JobListing]:
        response = client.get(POSTINGS_API_URL.format(token=token), params={"mode": "json"})
        response.raise_for_status()
        postings = response.json()

        company_name = LEVER_COMPANY_DISPLAY_NAMES.get(token, token.replace("-", " ").title())
        listings = []
        for posting in postings:
            categories = posting.get("categories") or {}
            description_parts = [posting.get("descriptionPlain", "")]
            for section in posting.get("lists") or []:
                heading = section.get("text", "")
                body = strip_html(section.get("content", ""))
                if heading or body:
                    description_parts.append(f"{heading}\n{body}")
            additional = posting.get("additionalPlain", "")
            if additional:
                description_parts.append(additional)

            listings.append(
                JobListing(
                    source="lever",
                    source_url=posting.get("hostedUrl", ""),
                    title=posting.get("text", ""),
                    company=company_name,
                    location=categories.get("location", "") or "",
                    salary_text=None,
                    description="\n\n".join(p for p in description_parts if p),
                    posted_date=_epoch_millis_to_iso(posting.get("createdAt")),
                    country_hint=posting.get("country") or None,
                )
            )
        return listings


def _epoch_millis_to_iso(epoch_millis: int | None) -> str | None:
    if not epoch_millis:
        return None
    return datetime.fromtimestamp(epoch_millis / 1000, tz=timezone.utc).isoformat()
