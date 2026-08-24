import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchFullPipelineStatus, runFullPipeline, stopFullPipeline, type FullPipelineStatus } from "../api/client";
import { ApplyRunPanel } from "./ApplyRunPanel";

const PHASE_LABELS: Record<FullPipelineStatus["phase"], string> = {
  "": "Not started",
  discover_score: "1/3 — Discover & Score",
  promote: "2/3 — Promoting to Pursue",
  auto_apply: "3/3 — Auto-Applying",
};

/** Elapsed-time-per-job in the auto-apply phase, extrapolated across
 * whatever's left — grounded in this run's own observed rate rather than a
 * guessed pre-run prediction, since discover/score/promote counts aren't
 * knowable before the run actually happens. */
function estimateTimeRemaining(status: FullPipelineStatus): string {
  if (status.phase !== "auto_apply" || !status.phase_started_at) return "—";
  const processed = status.submitted_count + status.failed_count + status.unsupported_count;
  const remaining = status.promoted_count - processed;
  if (remaining <= 0) return "wrapping up…";
  if (processed === 0) return "estimating…";
  const elapsedMs = Date.now() - new Date(status.phase_started_at).getTime();
  const perJobMs = elapsedMs / processed;
  const minutes = Math.round((perJobMs * remaining) / 60000);
  return minutes < 1 ? "< 1 min" : `~${minutes} min`;
}

function formatElapsed(startedAt: string | null): string {
  if (!startedAt) return "—";
  const totalSeconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

export function FullPipelinePanel({ discoverScoreAction }: { discoverScoreAction: ReactNode }) {
  const queryClient = useQueryClient();
  const wasRunning = useRef(false);
  const [scoreThreshold, setScoreThreshold] = useState("80");
  // Ticks once a second only so the elapsed/ETA text stays live between
  // polls — doesn't touch the network itself.
  const [, forceTick] = useState(0);

  const statusQuery = useQuery({
    queryKey: ["full-pipeline-status"],
    queryFn: fetchFullPipelineStatus,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1500 : 5000),
  });
  const isRunning = statusQuery.data?.status === "running";

  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(() => forceTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [isRunning]);

  useEffect(() => {
    if (wasRunning.current && !isRunning) {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["tracking-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["auto-apply-status"] });
    }
    wasRunning.current = isRunning;
  }, [isRunning, queryClient]);

  const runMutation = useMutation({
    mutationFn: () => runFullPipeline(Number(scoreThreshold)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["full-pipeline-status"] }),
  });
  const stopMutation = useMutation({
    mutationFn: stopFullPipeline,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["full-pipeline-status"] }),
  });

  const status = statusQuery.data;

  const handleRun = () => {
    const threshold = Number(scoreThreshold);
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 100) {
      window.alert("Score threshold must be a number between 0 and 100.");
      return;
    }
    if (
      window.confirm(
        `Run the full pipeline? This will discover new jobs, score them, automatically mark anything scoring ` +
          `${threshold}+ as Pursue, then submit REAL APPLICATIONS via Auto-Apply — no per-job review. ` +
          `Auto-Apply must already be enabled in Settings (this doesn't turn it on for you).`
      )
    ) {
      runMutation.mutate();
    }
  };

  const counts = status
    ? [
        { label: "Phase", value: PHASE_LABELS[status.phase] },
        { label: "Discovered", value: status.discovered },
        { label: "Scored", value: status.scored },
        { label: "Promoted", value: status.promoted_count },
        { label: "Applied", value: status.submitted_count },
        { label: "Failed", value: status.failed_count },
        { label: "Unsupported", value: status.unsupported_count },
        { label: "Elapsed", value: formatElapsed(status.started_at) },
        { label: "Est. remaining (apply phase)", value: estimateTimeRemaining(status) },
        { label: "Est. Claude API spend (this run)", value: `$${status.cost_usd_this_run.toFixed(2)}` },
      ]
    : [];

  return (
    <div className="full-pipeline-panel">
      <div className="full-pipeline-actions">
        {discoverScoreAction}
        <label className="field field-inline">
          <span className="field-label">Score threshold</span>
          <input
            type="number"
            min={0}
            max={100}
            value={scoreThreshold}
            onChange={(e) => setScoreThreshold(e.target.value)}
            disabled={isRunning}
            style={{ width: "4.5em" }}
          />
        </label>
        <ApplyRunPanel
          status={status}
          isRunning={isRunning}
          onRun={handleRun}
          onStop={() => stopMutation.mutate()}
          runPending={runMutation.isPending}
          stopPending={stopMutation.isPending}
          runLabel="Run Full Pipeline (Discover → Score → Auto-Apply)"
          runError={runMutation.error as Error | null}
          counts={counts}
        />
      </div>
    </div>
  );
}
