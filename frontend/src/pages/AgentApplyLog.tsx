import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchAgentApplyStatus,
  fetchApplications,
  runAgentApply,
  setupAgentApplyProfile,
  stopAgentApply,
  type AgentApplySetupProfileResult,
} from "../api/client";
import { ApplyRunPanel } from "../components/ApplyRunPanel";
import { ApplicationsTable } from "../components/ApplicationsTable";
import { UsageBanner } from "../components/UsageBanner";

export function AgentApplyLog() {
  const queryClient = useQueryClient();
  const wasRunning = useRef(false);
  const [setupResult, setSetupResult] = useState<AgentApplySetupProfileResult | null>(null);

  const statusQuery = useQuery({
    queryKey: ["agent-apply-status"],
    queryFn: fetchAgentApplyStatus,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1500 : 5000),
  });
  const applicationsQuery = useQuery({ queryKey: ["applications"], queryFn: fetchApplications });

  const isRunning = statusQuery.data?.status === "running";

  useEffect(() => {
    if (wasRunning.current && !isRunning) {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["tracking-jobs"] });
    }
    wasRunning.current = isRunning;
  }, [isRunning, queryClient]);

  const runMutation = useMutation({
    mutationFn: runAgentApply,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-apply-status"] }),
  });
  const stopMutation = useMutation({
    mutationFn: stopAgentApply,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-apply-status"] }),
  });
  const setupMutation = useMutation({
    mutationFn: setupAgentApplyProfile,
    onSuccess: (result) => setSetupResult(result),
  });

  return (
    <div>
      <p className="field-hint">
        Drives a real, visible Chrome window via an AI agent (the <code>claude</code> CLI) instead of hand-coded
        per-site selectors — works on sites without a built-in adapter, at the cost of a real API call per
        application. Uses the same applicant profile as Auto-Apply, configured in Settings.
      </p>
      <p className="field-hint">
        "Est. cost" below is <code>claude</code>'s own token-usage estimate (tokens used × standard API list
        price), not money actually spent under a subscription (flat fee, usage counted against your plan's
        rate limits instead). For the actual signal that matters under a subscription — cumulative tokens used
        and whether you're nearing your account's 5-hour rate-limit window — see the usage banner below (also
        shown at the top of Jobs and Tracking).
      </p>
      <UsageBanner />

      <div className="settings-actions">
        <button onClick={() => setupMutation.mutate()} disabled={setupMutation.isPending || isRunning}>
          {setupMutation.isPending ? "Opening…" : "Open Chrome to log in"}
        </button>
      </div>
      {setupMutation.isError && <p className="error-banner">{(setupMutation.error as Error).message}</p>}
      {setupResult && (
        <div className="status-banner">
          <p>{setupResult.note}</p>
          <p>Worth logging into, in the Chrome window that just opened:</p>
          <ul>
            {setupResult.suggested_logins.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <p>Close that window yourself when you're done — the session persists for future runs.</p>
        </div>
      )}

      <ApplyRunPanel
        status={statusQuery.data}
        isRunning={isRunning}
        onRun={() => runMutation.mutate()}
        onStop={() => stopMutation.mutate()}
        runPending={runMutation.isPending}
        stopPending={stopMutation.isPending}
        runLabel="Run Agent-Apply"
        runError={runMutation.error as Error | null}
        counts={[
          { label: "Submitted", value: statusQuery.data?.submitted_count ?? 0 },
          { label: "Failed", value: statusQuery.data?.failed_count ?? 0 },
          { label: "Unsupported", value: statusQuery.data?.unsupported_count ?? 0 },
          { label: "Est. cost", value: `$${(statusQuery.data?.total_cost_usd ?? 0).toFixed(3)}` },
        ]}
      />

      <h2>Log</h2>
      <ApplicationsTable query={applicationsQuery} />
    </div>
  );
}
