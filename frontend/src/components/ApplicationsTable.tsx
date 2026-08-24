import type { UseQueryResult } from "@tanstack/react-query";
import { applicationScreenshotUrl, type ApplicationOut } from "../api/client";

interface ApplicationsTableProps {
  query: UseQueryResult<ApplicationOut[]>;
}

/** Shared "what's been submitted" table for both the auto-apply and
 * agent-apply log pages — both write into the same applications table,
 * distinguished only by the `method` column. */
export function ApplicationsTable({ query }: ApplicationsTableProps) {
  if (query.isLoading) return <p>Loading…</p>;
  if (query.isError) return <p className="error-banner">{(query.error as Error).message}</p>;
  if (!query.data || query.data.length === 0) return <p className="empty-state">No applications submitted yet.</p>;

  return (
    <div className="table-scroll">
      <table className="jobs-table auto-apply-log-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Job</th>
            <th>Status</th>
            <th>Method</th>
            <th>Notes</th>
            <th>Screenshot</th>
          </tr>
        </thead>
        <tbody>
          {query.data.map((a) => (
            <tr key={a.id}>
              <td>{new Date(a.created_at).toLocaleString()}</td>
              <td>
                {a.job_title} @ {a.job_company}
              </td>
              <td className={`status-${a.status}`}>{a.status}</td>
              <td>{a.method}</td>
              <td>{a.notes}</td>
              <td>
                {a.screenshot_path ? (
                  <a href={applicationScreenshotUrl(a.id)} target="_blank" rel="noreferrer">
                    view
                  </a>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
