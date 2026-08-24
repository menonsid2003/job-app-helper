import { useMemo, useState } from "react";
import { TRACKING_STATUS_OPTIONS, type JobOut } from "../api/client";

export type SortKey =
  | "score" | "title" | "company" | "location" | "is_remote" | "role_category" | "sponsorship" | "status" | "source";
export type SortDir = "asc" | "desc";

// Ranked rather than alphabetical so sorting actually clusters "sponsors"
// together at one end — that's the whole point of being able to sort this
// column, not just alphabetize three category labels.
const SPONSORSHIP_RANK: Record<string, number> = { yes: 2, not_mentioned: 1, no: 0 };

// Natural pipeline order (Track -> Tailored -> Applied -> ...) rather than
// alphabetical, same reasoning as sponsorship above. Statuses outside the
// Tracking Table's own list (e.g. "excluded") sort last.
const STATUS_RANK: Record<string, number> = Object.fromEntries(
  TRACKING_STATUS_OPTIONS.map((status, i) => [status, i])
);

function sortValue(job: JobOut, key: SortKey): string | number {
  switch (key) {
    case "score":
      return job.latest_score?.score ?? -1;
    case "role_category":
      return job.latest_score?.role_category ?? "";
    case "sponsorship":
      return SPONSORSHIP_RANK[job.latest_score?.work_authorization.sponsorship_mentioned ?? "not_mentioned"] ?? 1;
    case "status":
      return STATUS_RANK[job.status] ?? TRACKING_STATUS_OPTIONS.length;
    case "is_remote":
      // Unknown sorts between the two known states, same reasoning as
      // sponsorship's "not_mentioned" sitting between "sponsors"/"won't".
      return job.is_remote === true ? 2 : job.is_remote === false ? 0 : 1;
    default:
      return job[key] ?? "";
  }
}

/** Shared sort/filter state + derived visible-jobs list for any table of
 * JobOut rows — used by both the Jobs and Tracking tables so their
 * filtering/sorting behavior (and any future fix to it) stays identical. */
export function useJobFilters(jobs: JobOut[], defaultSortKey: SortKey = "score") {
  const [sortKey, setSortKey] = useState<SortKey>(defaultSortKey);
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [roleCategoryFilter, setRoleCategoryFilter] = useState("");
  const [sponsorshipFilter, setSponsorshipFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [locationFilter, setLocationFilter] = useState("");
  const [remoteFilter, setRemoteFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  const roleCategories = useMemo(
    () => Array.from(new Set(jobs.map((j) => j.latest_score?.role_category).filter((v): v is string => !!v))).sort(),
    [jobs]
  );
  const sources = useMemo(() => Array.from(new Set(jobs.map((j) => j.source).filter(Boolean))).sort(), [jobs]);
  const locations = useMemo(() => Array.from(new Set(jobs.map((j) => j.location).filter(Boolean))).sort(), [jobs]);

  const visibleJobs = useMemo(() => {
    let filtered = jobs;
    if (roleCategoryFilter) {
      filtered = filtered.filter((j) => j.latest_score?.role_category === roleCategoryFilter);
    }
    if (sponsorshipFilter) {
      filtered = filtered.filter(
        (j) => (j.latest_score?.work_authorization.sponsorship_mentioned ?? "not_mentioned") === sponsorshipFilter
      );
    }
    if (sourceFilter) {
      filtered = filtered.filter((j) => j.source === sourceFilter);
    }
    if (locationFilter) {
      filtered = filtered.filter((j) => j.location === locationFilter);
    }
    if (remoteFilter) {
      filtered = filtered.filter((j) =>
        remoteFilter === "remote" ? j.is_remote === true
        : remoteFilter === "not_remote" ? j.is_remote === false
        : j.is_remote === null
      );
    }
    const query = searchQuery.trim().toLowerCase();
    if (query) {
      filtered = filtered.filter(
        (j) => j.company.toLowerCase().includes(query) || j.title.toLowerCase().includes(query)
      );
    }
    return [...filtered].sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [jobs, sortKey, sortDir, roleCategoryFilter, sponsorshipFilter, sourceFilter, locationFilter, remoteFilter, searchQuery]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "score" || key === "sponsorship" ? "desc" : "asc");
    }
  };

  return {
    sortKey, sortDir, toggleSort,
    roleCategoryFilter, setRoleCategoryFilter,
    sponsorshipFilter, setSponsorshipFilter,
    sourceFilter, setSourceFilter,
    locationFilter, setLocationFilter,
    remoteFilter, setRemoteFilter,
    searchQuery, setSearchQuery,
    roleCategories, sources, locations, visibleJobs,
  };
}
