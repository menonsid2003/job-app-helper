import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchPipelineStatus, runPipeline, stopPipeline } from "./api/client";
import { PipelineProgress } from "./components/PipelineProgress";
import { FullPipelinePanel } from "./components/FullPipelinePanel";
import { LlmUsageBanner } from "./components/LlmUsageBanner";
import { Dashboard } from "./pages/Dashboard";
import { Tracking } from "./pages/Tracking";
import { Settings } from "./pages/Settings";
import { AutoApplyLog } from "./pages/AutoApplyLog";
import { AgentApplyLog } from "./pages/AgentApplyLog";

type Page = "jobs" | "tracking" | "auto-apply" | "agent-apply" | "settings";

export function App() {
  const [page, setPage] = useState<Page>("jobs");
  const queryClient = useQueryClient();
  const wasRunning = useRef(false);

  const statusQuery = useQuery({
    queryKey: ["pipeline-status"],
    queryFn: fetchPipelineStatus,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1000 : 4000),
  });

  const isRunning = statusQuery.data?.status === "running";

  useEffect(() => {
    if (wasRunning.current && !isRunning) {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["tracking-jobs"] });
    }
    wasRunning.current = isRunning;
  }, [isRunning, queryClient]);

  const pipelineMutation = useMutation({
    mutationFn: runPipeline,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pipeline-status"] }),
  });

  const stopMutation = useMutation({
    mutationFn: stopPipeline,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pipeline-status"] }),
  });

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Job Application Helper</h1>
      </header>

      <LlmUsageBanner />

      <FullPipelinePanel
        discoverScoreAction={
          isRunning ? (
            <button onClick={() => stopMutation.mutate()} disabled={stopMutation.isPending || statusQuery.data?.stop_requested}>
              {statusQuery.data?.stop_requested ? "Stopping…" : "Stop"}
            </button>
          ) : (
            <button onClick={() => pipelineMutation.mutate()} disabled={pipelineMutation.isPending}>
              Discover & Score
            </button>
          )
        }
      />

      {pipelineMutation.isError && <p className="error-banner">{(pipelineMutation.error as Error).message}</p>}
      {stopMutation.isError && <p className="error-banner">{(stopMutation.error as Error).message}</p>}

      {statusQuery.data &&
        (statusQuery.data.status === "running" ||
          statusQuery.data.status === "error" ||
          statusQuery.data.status === "stopped") && <PipelineProgress status={statusQuery.data} />}

      {statusQuery.data?.status === "done" && (
        <p className="status-banner">
          Last run: discovered {statusQuery.data.discovered}, scored {statusQuery.data.scored}, excluded{" "}
          {statusQuery.data.excluded}, skipped (low relevance) {statusQuery.data.skipped_low_relevance}, deduped{" "}
          {statusQuery.data.deduped_skipped}.
        </p>
      )}

      <nav className="tabs page-tabs">
        <button className={page === "jobs" ? "active" : ""} onClick={() => setPage("jobs")}>
          Jobs
        </button>
        <button className={page === "tracking" ? "active" : ""} onClick={() => setPage("tracking")}>
          Tracking
        </button>
        <button className={page === "agent-apply" ? "active" : ""} onClick={() => setPage("agent-apply")}>
          Agent Apply
        </button>
        <button className={page === "auto-apply" ? "active" : ""} onClick={() => setPage("auto-apply")}>
          Auto-Apply Log
        </button>
        <button className={page === "settings" ? "active" : ""} onClick={() => setPage("settings")}>
          Settings
        </button>
      </nav>

      {page === "jobs" && <Dashboard />}
      {page === "tracking" && <Tracking />}
      {page === "auto-apply" && <AutoApplyLog />}
      {page === "agent-apply" && <AgentApplyLog />}
      {page === "settings" && <Settings />}
    </div>
  );
}
