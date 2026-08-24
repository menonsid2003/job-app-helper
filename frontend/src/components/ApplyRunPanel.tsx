interface RunStateShape {
  status: "idle" | "running" | "done" | "error" | "stopped";
  current_step: string;
  error: string | null;
  stop_requested: boolean;
  logs: string[];
}

interface ApplyRunPanelProps {
  status: RunStateShape | undefined;
  isRunning: boolean;
  onRun: () => void;
  onStop: () => void;
  runPending: boolean;
  stopPending: boolean;
  runLabel: string;
  runError?: Error | null;
  /** e.g. [{label: "Submitted", value: 3}, {label: "Failed", value: 1}] —
   * different run types (auto-apply, agent-apply, tailor-all) track
   * different counters, so the panel doesn't assume a fixed set. */
  counts: { label: string; value: number | string }[];
}

/** Shared run/stop + progress/log panel — the run-state shape (status/logs/
 * counters) is structurally the same across auto-apply, agent-apply, and
 * tailor-all on the backend, just with different counter names. */
export function ApplyRunPanel({
  status, isRunning, onRun, onStop, runPending, stopPending, runLabel, runError, counts,
}: ApplyRunPanelProps) {
  return (
    <>
      <div className="settings-actions">
        {isRunning ? (
          <button onClick={onStop} disabled={stopPending || status?.stop_requested}>
            {status?.stop_requested ? "Stopping…" : "Stop"}
          </button>
        ) : (
          <button onClick={onRun} disabled={runPending}>
            {runLabel}
          </button>
        )}
      </div>

      {runError && <p className="error-banner">{runError.message}</p>}

      {status && status.status !== "idle" && (
        <div className={`pipeline-progress pipeline-progress-${status.status}`}>
          <div className="pipeline-progress-header">
            {isRunning && <span className="spinner" aria-hidden="true" />}
            <strong>{status.status === "error" ? `Failed: ${status.error}` : status.current_step}</strong>
          </div>
          <div className="pipeline-progress-counts">
            {counts.map((c) => (
              <span key={c.label}>
                {c.label}: {c.value}
              </span>
            ))}
          </div>
          <pre className="pipeline-log">{status.logs.join("\n")}</pre>
        </div>
      )}
    </>
  );
}
