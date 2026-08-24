import { useQuery } from "@tanstack/react-query";
import { fetchAgentApplyUsage } from "../api/client";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatResetsAt(iso: string | null): string | null {
  if (!iso) return null;
  const resetsAt = new Date(iso);
  const minutesLeft = Math.round((resetsAt.getTime() - Date.now()) / 60000);
  if (minutesLeft <= 0) return "resets shortly";
  const hours = Math.floor(minutesLeft / 60);
  const minutes = minutesLeft % 60;
  const parts = [hours > 0 ? `${hours}h` : null, `${minutes}m`].filter(Boolean);
  return `resets in ${parts.join(" ")}`;
}

/** Cumulative agent-apply token usage + the account's last-seen 5-hour
 * rate-limit window status, since the `claude` CLI runs against your Claude
 * subscription's session limits, not metered per-token billing — the
 * dollar "Est. cost" shown on the Agent Apply tab doesn't reflect that.
 * Persisted server-side (app/agent_apply_usage.py), so this shows real
 * numbers even when no run is currently active. */
export function UsageBanner() {
  const usageQuery = useQuery({
    queryKey: ["agent-apply-usage"],
    queryFn: fetchAgentApplyUsage,
    refetchInterval: 30_000,
  });

  const usage = usageQuery.data;
  if (!usage) return null;

  if (usage.job_count === 0) {
    return (
      <div className="usage-banner usage-banner-muted">
        No Agent Apply runs yet — cumulative token usage and your Claude subscription's rate-limit status will
        show here once it's run at least once.
      </div>
    );
  }

  const totalTokens =
    usage.total_input_tokens + usage.total_output_tokens + usage.total_cache_read_tokens + usage.total_cache_creation_tokens;
  const resets = formatResetsAt(usage.rate_limit_resets_at);
  const atLimit = usage.rate_limit_status !== null && usage.rate_limit_status !== "allowed";

  return (
    <div className="usage-banner" title="Cumulative token usage across all Agent Apply runs, plus your Claude subscription's rate-limit window status">
      <span className="usage-banner-item">
        <strong>{formatTokens(totalTokens)}</strong> agent-apply tokens used
      </span>
      <span className="usage-banner-item usage-banner-muted">
        ({formatTokens(usage.total_input_tokens)} in · {formatTokens(usage.total_output_tokens)} out ·{" "}
        {formatTokens(usage.total_cache_read_tokens)} cached · ~${usage.total_cost_usd.toFixed(2)} equiv.)
      </span>
      {usage.rate_limit_status && (
        <span className={`usage-banner-item usage-banner-pill ${atLimit ? "usage-banner-warn" : "usage-banner-ok"}`}>
          {usage.rate_limit_type ?? "session"} window: {usage.rate_limit_status}
          {resets ? ` · ${resets}` : ""}
        </span>
      )}
    </div>
  );
}
