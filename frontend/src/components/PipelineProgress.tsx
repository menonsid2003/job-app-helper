import { useEffect, useRef } from "react";
import type { PipelineStatus } from "../api/client";

export function PipelineProgress({ status }: { status: PipelineStatus }) {
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [status.logs.length]);

  const headerText =
    status.status === "error"
      ? `Pipeline failed: ${status.error}`
      : status.status === "stopped"
        ? "Pipeline stopped."
        : status.current_step;

  return (
    <div className={`pipeline-progress pipeline-progress-${status.status}`}>
      <div className="pipeline-progress-header">
        {status.status === "running" && <span className="spinner" aria-hidden="true" />}
        <strong>{headerText}</strong>
      </div>
      <div className="pipeline-progress-counts">
        <span>Discovered: {status.discovered}</span>
        <span>Scored: {status.scored}</span>
        <span>Excluded: {status.excluded}</span>
        <span>Skipped: {status.skipped_low_relevance}</span>
        <span>Deduped: {status.deduped_skipped}</span>
      </div>
      <pre className="pipeline-log" ref={logRef}>
        {status.logs.join("\n")}
      </pre>
    </div>
  );
}
