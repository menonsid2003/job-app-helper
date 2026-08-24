import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { updateJob, type JobOut, type JobStatus } from "../api/client";
import { EditableCell } from "./EditableCell";
import { ExpandableText } from "./ExpandableText";
import { JobFilterBar } from "./JobFilterBar";
import { PaginationControls } from "./PaginationControls";
import { RemoteSelect } from "./RemoteSelect";
import { SortableHeader } from "./SortableHeader";
import { useJobFilters } from "../hooks/useJobFilters";
import { usePagination } from "../hooks/usePagination";
import { scoreClass } from "../lib/score";

// A clearance requirement is a hard blocker on its own — showing it next to
// a "not mentioned"/"sponsors" sponsorship badge implied there were two
// separate open questions when there's really just one reason this job is a
// no-go, so it replaces the sponsorship badge rather than sitting beside it.
function sponsorshipBadge(job: JobOut) {
  if (job.latest_score?.work_authorization.security_clearance_required) {
    return <span className="badge badge-bad">clearance required</span>;
  }
  const mentioned = job.latest_score?.work_authorization.sponsorship_mentioned;
  if (mentioned === "yes") return <span className="badge badge-good">sponsors</span>;
  if (mentioned === "no") return <span className="badge badge-bad">won't sponsor</span>;
  return <span className="badge badge-neutral">not mentioned</span>;
}

function ActionButtons({ job }: { job: JobOut }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (status: JobStatus) => updateJob(job.id, { status }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  return (
    <div className="action-buttons">
      <button className="action-pursue" onClick={() => mutation.mutate("pursue")} disabled={mutation.isPending}>
        Track
      </button>
      <button className="action-skip" onClick={() => mutation.mutate("skip")} disabled={mutation.isPending}>
        Exclude
      </button>
    </div>
  );
}

function JobRow({
  job, showActions, selectable, selected, onToggleSelect,
}: {
  job: JobOut; showActions: boolean; selectable: boolean; selected: boolean; onToggleSelect: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  // Same underlying Job row Tracking reads — editing company/location here
  // shows up there too (and vice versa) since both invalidate ["jobs"] /
  // ["tracking-jobs"] against one source of truth, not a copy.
  const editMutation = useMutation({
    mutationFn: (update: { company?: string; location?: string; is_remote?: boolean | null }) =>
      updateJob(job.id, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["tracking-jobs"] });
    },
  });

  return (
    <tr className={selected ? "row-selected" : ""}>
      {selectable && (
        <td>
          <input type="checkbox" checked={selected} onChange={() => onToggleSelect(job.id)} />
        </td>
      )}
      <td className={scoreClass(job.latest_score?.score)}>{job.latest_score?.score ?? "—"}</td>
      <td>{job.title}</td>
      <td>
        <EditableCell value={job.company} onSave={(company) => editMutation.mutate({ company })} placeholder="Company…" />
      </td>
      <td>
        <EditableCell value={job.location} onSave={(location) => editMutation.mutate({ location })} placeholder="Location…" />
      </td>
      <td>
        <RemoteSelect value={job.is_remote} onSave={(is_remote) => editMutation.mutate({ is_remote })} />
      </td>
      <td>{job.latest_score?.role_category ?? "—"}</td>
      <td>{sponsorshipBadge(job)}</td>
      <ExpandableText className="reason-cell" text={job.latest_score?.reasoning ?? ""} />
      <td>{job.source}</td>
      <td>
        <a href={job.canonical_url ?? job.source_url} target="_blank" rel="noreferrer">
          Apply
        </a>
      </td>
      {showActions && (
        <td>
          <ActionButtons job={job} />
        </td>
      )}
    </tr>
  );
}

function BatchActionBar({ selectedIds, onDone }: { selectedIds: Set<number>; onDone: () => void }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (status: JobStatus) => Promise.all([...selectedIds].map((id) => updateJob(id, { status }))),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["tracking-jobs"] });
      onDone();
    },
  });

  if (selectedIds.size === 0) return null;

  return (
    <div className="batch-action-bar">
      <span>{selectedIds.size} selected</span>
      <button className="action-pursue" onClick={() => mutation.mutate("pursue")} disabled={mutation.isPending}>
        Track selected
      </button>
      <button className="action-skip" onClick={() => mutation.mutate("skip")} disabled={mutation.isPending}>
        Exclude selected
      </button>
      <button onClick={onDone} disabled={mutation.isPending}>
        Clear selection
      </button>
      {mutation.isError && <span className="error-banner inline">{(mutation.error as Error).message}</span>}
    </div>
  );
}

export function JobsTable({ jobs, showActions = false }: { jobs: JobOut[]; showActions?: boolean }) {
  const {
    sortKey, sortDir, toggleSort,
    roleCategoryFilter, setRoleCategoryFilter,
    sponsorshipFilter, setSponsorshipFilter,
    sourceFilter, setSourceFilter,
    locationFilter, setLocationFilter,
    remoteFilter, setRemoteFilter,
    searchQuery, setSearchQuery,
    roleCategories, sources, locations, visibleJobs,
  } = useJobFilters(jobs);

  const { page, setPage, pageSize, setPageSize, pageCount, pageItems, rangeStart, rangeEnd } = usePagination(visibleJobs);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  if (jobs.length === 0) {
    return <p className="empty-state">No jobs to show yet. Run the pipeline to discover and score jobs.</p>;
  }

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const pageIds = pageItems.map((j) => j.id);
  const allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));

  const toggleSelectPage = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        pageIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  return (
    <div>
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

      {showActions && <BatchActionBar selectedIds={selectedIds} onDone={() => setSelectedIds(new Set())} />}

      <div className="table-scroll">
        <table className="jobs-table">
          <thead>
            <tr>
              {showActions && (
                <th>
                  <input type="checkbox" checked={allOnPageSelected} onChange={toggleSelectPage} title="Select all on this page" />
                </th>
              )}
              <SortableHeader label="Score" sortKey="score" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Title" sortKey="title" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Company" sortKey="company" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Location" sortKey="location" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Remote" sortKey="is_remote" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Role category" sortKey="role_category" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Sponsorship" sortKey="sponsorship" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
              <th>Reason</th>
              <SortableHeader label="Source" sortKey="source" currentSortKey={sortKey} currentSortDir={sortDir} onClick={toggleSort} />
              <th>Link</th>
              {showActions && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {pageItems.map((job) => (
              <JobRow
                key={job.id}
                job={job}
                showActions={showActions}
                selectable={showActions}
                selected={selectedIds.has(job.id)}
                onToggleSelect={toggleSelect}
              />
            ))}
          </tbody>
        </table>
      </div>

      <PaginationControls
        page={page}
        pageCount={pageCount}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
        rangeStart={rangeStart}
        rangeEnd={rangeEnd}
        totalCount={visibleJobs.length}
      />
    </div>
  );
}
