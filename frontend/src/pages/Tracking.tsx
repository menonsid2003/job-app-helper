import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchTailorAllStatus,
  fetchTrackingJobs,
  runTailorAll,
  stopTailorAll,
  updateJob,
  STATUS_LABELS,
  TRACKING_STATUS_OPTIONS,
  type JobOut,
  type JobStatus,
} from "../api/client";
import { ResumePanel } from "../components/ResumePanel";
import { ApplyRunPanel } from "../components/ApplyRunPanel";
import { EditableCell } from "../components/EditableCell";
import { JobFilterBar } from "../components/JobFilterBar";
import { RemoteSelect } from "../components/RemoteSelect";
import { SortableHeader } from "../components/SortableHeader";
import { useJobFilters } from "../hooks/useJobFilters";
import { scoreClass } from "../lib/score";
import { UsageBanner } from "../components/UsageBanner";

const TRACKING_COLUMN_COUNT = 13;

function sponsorshipBadge(job: JobOut) {
  const mentioned = job.latest_score?.work_authorization.sponsorship_mentioned;
  if (mentioned === "yes") return <span className="badge badge-good">sponsors</span>;
  if (mentioned === "no") return <span className="badge badge-bad">won't sponsor</span>;
  return <span className="badge badge-neutral">not mentioned</span>;
}

function TrackingRow({ job }: { job: JobOut }) {
  const queryClient = useQueryClient();
  const [resumeExpanded, setResumeExpanded] = useState(false);

  // Same Job row the Jobs listing edits too — invalidating both queries
  // keeps whichever tab you're not on in sync next time you switch to it.
  const updateMutation = useMutation({
    mutationFn: (
      update: { status?: JobStatus; notes?: string; company?: string; location?: string; is_remote?: boolean | null }
    ) => updateJob(job.id, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tracking-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  return (
    <>
      <tr>
        <td className={scoreClass(job.latest_score?.score)}>{job.latest_score?.score ?? "—"}</td>
        <td>{job.title}</td>
        <td>
          <EditableCell value={job.company} onSave={(company) => updateMutation.mutate({ company })} placeholder="Company…" />
        </td>
        <td>
          <EditableCell value={job.location} onSave={(location) => updateMutation.mutate({ location })} placeholder="Location…" />
        </td>
        <td>
          <RemoteSelect value={job.is_remote} onSave={(is_remote) => updateMutation.mutate({ is_remote })} />
        </td>
        <td>{job.latest_score?.role_category ?? "—"}</td>
        <td>{sponsorshipBadge(job)}</td>
        <td>
          <a href={job.canonical_url ?? job.source_url} target="_blank" rel="noreferrer">
            Apply
          </a>
        </td>
        <td>
          <select
            value={job.status}
            onChange={(e) => updateMutation.mutate({ status: e.target.value as JobStatus })}
          >
            {TRACKING_STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </td>
        <td>
          <EditableCell value={job.notes} onSave={(notes) => updateMutation.mutate({ notes })} placeholder="Notes…" />
        </td>
        <td>{new Date(job.last_updated).toLocaleString()}</td>
        <td>
          <button onClick={() => setResumeExpanded((e) => !e)}>{resumeExpanded ? "Hide resume" : "Resume"}</button>
        </td>
        <td>
          <button
            className="action-skip"
            onClick={() => {
              if (window.confirm(`Remove "${job.title}" @ ${job.company} from Tracking?`)) {
                updateMutation.mutate({ status: "skip" });
              }
            }}
            disabled={updateMutation.isPending}
            title="Same as Exclude on the Jobs tab — shows up under Excluded, and can be Tracked again from there."
          >
            Remove
          </button>
        </td>
      </tr>
      {resumeExpanded && (
        <tr>
          <td colSpan={TRACKING_COLUMN_COUNT}>
            <ResumePanel jobId={job.id} />
          </td>
        </tr>
      )}
    </>
  );
}

function TailorAllPanel() {
  const queryClient = useQueryClient();
  const wasRunning = useRef(false);

  const statusQuery = useQuery({
    queryKey: ["tailor-all-status"],
    queryFn: fetchTailorAllStatus,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1500 : 5000),
  });
  const isRunning = statusQuery.data?.status === "running";

  useEffect(() => {
    if (wasRunning.current && !isRunning) {
      queryClient.invalidateQueries({ queryKey: ["tracking-jobs"] });
    }
    wasRunning.current = isRunning;
  }, [isRunning, queryClient]);

  const runMutation = useMutation({
    mutationFn: runTailorAll,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tailor-all-status"] }),
  });
  const stopMutation = useMutation({
    mutationFn: stopTailorAll,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tailor-all-status"] }),
  });

  return (
    <ApplyRunPanel
      status={statusQuery.data}
      isRunning={isRunning}
      onRun={() => runMutation.mutate()}
      onStop={() => stopMutation.mutate()}
      runPending={runMutation.isPending}
      stopPending={stopMutation.isPending}
      runLabel="Tailor All (untailored, tracked jobs)"
      runError={runMutation.error as Error | null}
      counts={[
        { label: "Tailored", value: statusQuery.data?.tailored_count ?? 0 },
        { label: "Failed", value: statusQuery.data?.failed_count ?? 0 },
      ]}
    />
  );
}

export function Tracking() {
  const jobsQuery = useQuery({ queryKey: ["tracking-jobs"], queryFn: fetchTrackingJobs });
  const jobs = jobsQuery.data ?? [];
  const {
    sortKey, sortDir, toggleSort,
    roleCategoryFilter, setRoleCategoryFilter,
    sponsorshipFilter, setSponsorshipFilter,
    sourceFilter, setSourceFilter,
    locationFilter, setLocationFilter,
    remoteFilter, setRemoteFilter,
    searchQuery, setSearchQuery,
    roleCategories, sources, locations, visibleJobs,
  } = useJobFilters(jobs, "score");

  return (
    <div>
      <UsageBanner />
      <TailorAllPanel />

      {jobsQuery.isLoading && <p>Loading…</p>}
      {jobsQuery.isError && <p className="error-banner">{(jobsQuery.error as Error).message}</p>}
      {jobsQuery.data && jobs.length === 0 && (
        <p className="empty-state">Nothing here yet. Mark a job "Track" from the Jobs tab to track it here.</p>
      )}
      {jobsQuery.data && jobs.length > 0 && (
        <>
          <JobFilterBar
            roleCategories={roleCategories}
            roleCategoryFilter={roleCategoryFilter}
            onRoleCategoryChange={setRoleCategoryFilter}
            sponsorshipFilter={sponsorshipFilter}
            onSponsorshipChange={setSponsorshipFilter}
            sources={sources}
            sourceFilter={sourceFilter}
            onSourceChange={setSourceFilter}
            locations={locations}
            locationFilter={locationFilter}
            onLocationChange={setLocationFilter}
            remoteFilter={remoteFilter}
            onRemoteChange={setRemoteFilter}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            visibleCount={visibleJobs.length}
            totalCount={jobs.length}
          />
          <div className="table-scroll">
            <table className="jobs-table">
              <thead>
                <tr>
                  <SortableHeader label="Score" sortKey="score" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Title" sortKey="title" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Company" sortKey="company" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Location" sortKey="location" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Remote" sortKey="is_remote" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Role category" sortKey="role_category" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Sponsorship" sortKey="sponsorship" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
                  <th>Link</th>
                  <SortableHeader label="Status" sortKey="status" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
                  <th>Notes</th>
                  <th>Last updated</th>
                  <th>Resume</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {visibleJobs.map((job) => (
                  <TrackingRow key={job.id} job={job} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
