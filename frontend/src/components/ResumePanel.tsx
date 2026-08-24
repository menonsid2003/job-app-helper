import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteResume,
  fetchResumeDiff,
  fetchResumesForJob,
  resumePdfUrl,
  resumeTextUrl,
  tailorResume,
} from "../api/client";

function DiffView({ resumeId }: { resumeId: number }) {
  const diffQuery = useQuery({ queryKey: ["resume-diff", resumeId], queryFn: () => fetchResumeDiff(resumeId) });

  if (diffQuery.isLoading) return <p>Loading diff…</p>;
  if (diffQuery.isError) return <p className="error-banner">{(diffQuery.error as Error).message}</p>;

  return (
    <pre className="resume-diff">
      {diffQuery.data!.map((line, i) => (
        <div key={i} className={`diff-line diff-${line.type}`}>
          {line.type === "added" ? "+ " : line.type === "removed" ? "- " : "  "}
          {line.text}
        </div>
      ))}
    </pre>
  );
}

export function ResumePanel({ jobId }: { jobId: number }) {
  const queryClient = useQueryClient();
  const [expandedVersion, setExpandedVersion] = useState<number | null>(null);
  const [correction, setCorrection] = useState("");

  const resumesQuery = useQuery({ queryKey: ["resumes", jobId], queryFn: () => fetchResumesForJob(jobId) });
  const hasResumes = (resumesQuery.data?.length ?? 0) > 0;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["resumes", jobId] });

  const tailorMutation = useMutation({
    mutationFn: () => tailorResume(jobId),
    onSuccess: invalidate,
  });
  const regenerateMutation = useMutation({
    mutationFn: () => tailorResume(jobId, correction),
    onSuccess: () => {
      setCorrection("");
      invalidate();
    },
  });
  const deleteMutation = useMutation({
    mutationFn: deleteResume,
    onSuccess: invalidate,
  });

  return (
    <div className="resume-panel">
      <div className="settings-actions">
        <button onClick={() => tailorMutation.mutate()} disabled={tailorMutation.isPending}>
          {tailorMutation.isPending ? "Tailoring…" : hasResumes ? "Tailor Fresh (new version)" : "Tailor Resume"}
        </button>
      </div>
      {tailorMutation.isError && <p className="error-banner">{(tailorMutation.error as Error).message}</p>}

      {hasResumes && (
        <div className="resume-correction">
          <label className="field">
            <span className="field-label">Not quite right? Describe the fix, then regenerate</span>
            <textarea
              rows={2}
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
              placeholder="e.g. Remove the bullet claiming I led the migration — I was a contributor, not the lead."
            />
          </label>
          <button
            onClick={() => regenerateMutation.mutate()}
            disabled={regenerateMutation.isPending || correction.trim() === ""}
          >
            {regenerateMutation.isPending ? "Regenerating…" : "Regenerate with correction (new version)"}
          </button>
          {regenerateMutation.isError && <p className="error-banner">{(regenerateMutation.error as Error).message}</p>}
        </div>
      )}

      {resumesQuery.data && resumesQuery.data.length > 0 && (
        <ul className="resume-version-list">
          {resumesQuery.data.map((resume) => (
            <li key={resume.id}>
              <div className="resume-version-row">
                <strong>v{resume.version}</strong>
                <span className="resume-diff-summary">{resume.diff_summary}</span>
                <a href={resumePdfUrl(resume.id)} target="_blank" rel="noreferrer">
                  PDF
                </a>
                <a href={resumeTextUrl(resume.id)} target="_blank" rel="noreferrer">
                  Text
                </a>
                <button onClick={() => setExpandedVersion(expandedVersion === resume.id ? null : resume.id)}>
                  {expandedVersion === resume.id ? "Hide diff" : "View diff"}
                </button>
                <button
                  className="action-skip"
                  onClick={() => {
                    if (window.confirm(`Delete resume v${resume.version}? This can't be undone.`)) {
                      deleteMutation.mutate(resume.id);
                    }
                  }}
                  disabled={deleteMutation.isPending}
                >
                  Delete
                </button>
              </div>
              {expandedVersion === resume.id && <DiffView resumeId={resume.id} />}
            </li>
          ))}
        </ul>
      )}
      {deleteMutation.isError && <p className="error-banner">{(deleteMutation.error as Error).message}</p>}
    </div>
  );
}
