import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchApplications, fetchAutoApplyStatus, runAutoApply, stopAutoApply } from "../api/client";
import { ApplyRunPanel } from "../components/ApplyRunPanel";
import { ApplicationsTable } from "../components/ApplicationsTable";

export function AutoApplyLog() {
  const queryClient = useQueryClient();
  const wasRunning = useRef(false);

  const statusQuery = useQuery({
    queryKey: ["auto-apply-status"],
    queryFn: fetchAutoApplyStatus,
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
    mutationFn: runAutoApply,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auto-apply-status"] }),
  });
  const stopMutation = useMutation({
    mutationFn: stopAutoApply,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auto-apply-status"] }),
  });

  return (
    <div>
      <ApplyRunPanel
        status={statusQuery.data}
        isRunning={isRunning}
        onRun={() => runMutation.mutate()}
        onStop={() => stopMutation.mutate()}
        runPending={runMutation.isPending}
        stopPending={stopMutation.isPending}
        runLabel="Run Auto-Apply"
        runError={runMutation.error as Error | null}
        counts={[
          { label: "Submitted", value: statusQuery.data?.submitted_count ?? 0 },
          { label: "Failed", value: statusQuery.data?.failed_count ?? 0 },
          { label: "Unsupported", value: statusQuery.data?.unsupported_count ?? 0 },
        ]}
      />

      <h2>Log</h2>
      <ApplicationsTable query={applicationsQuery} />
    </div>
  );
}
