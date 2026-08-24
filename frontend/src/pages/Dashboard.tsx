import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchExcludedJobs, fetchJobs } from "../api/client";
import { JobsTable } from "../components/JobsTable";
import { UsageBanner } from "../components/UsageBanner";

type Tab = "scored" | "excluded";

export function Dashboard() {
  const [tab, setTab] = useState<Tab>("scored");

  const jobsQuery = useQuery({
    queryKey: ["jobs", tab],
    queryFn: () => (tab === "scored" ? fetchJobs() : fetchExcludedJobs()),
  });

  return (
    <div>
      <UsageBanner />
      <nav className="tabs">
        <button className={tab === "scored" ? "active" : ""} onClick={() => setTab("scored")}>
          Scored Jobs
        </button>
        <button className={tab === "excluded" ? "active" : ""} onClick={() => setTab("excluded")}>
          Excluded
        </button>
      </nav>

      {jobsQuery.isLoading && <p>Loading…</p>}
      {jobsQuery.isError && <p className="error-banner">{(jobsQuery.error as Error).message}</p>}
      {jobsQuery.data && <JobsTable jobs={jobsQuery.data} showActions />}
    </div>
  );
}
