import { useQuery } from "@tanstack/react-query";
import { fetchScoringUsage } from "../api/client";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Cumulative Score + Tailor token usage and estimated cost against the
 * Anthropic API (backend/app/scoring_usage.py). Only shows real numbers
 * when LLM_PROVIDER=anthropic — scoring/tailoring against local Ollama is
 * free and untracked. There's no Anthropic endpoint that reports your
 * actual account credit balance, so "cost" here is an estimate computed
 * from token counts against current list pricing, not a live balance. */
export function LlmUsageBanner() {
  const usageQuery = useQuery({
    queryKey: ["llm-usage"],
    queryFn: fetchScoringUsage,
    refetchInterval: 15_000,
  });

  const usage = usageQuery.data;
  if (!usage || usage.call_count === 0) return null;

  const totalTokens =
    usage.total_input_tokens + usage.total_output_tokens + usage.total_cache_read_tokens + usage.total_cache_creation_tokens;

  return (
    <div
      className="usage-banner"
      title="Cumulative Score + Tailor token usage against the Anthropic API, estimated from list pricing — not a live account balance"
    >
      <span className="usage-banner-item">
        <strong>${usage.total_cost_usd.toFixed(2)}</strong> est. Claude API spend
      </span>
      <span className="usage-banner-item usage-banner-muted">
        ({formatTokens(totalTokens)} tokens · {usage.call_count} call{usage.call_count === 1 ? "" : "s"} · {formatTokens(usage.total_input_tokens)}{" "}
        in · {formatTokens(usage.total_output_tokens)} out)
      </span>
    </div>
  );
}
