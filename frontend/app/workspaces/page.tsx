"use client";

import { useQuery } from "@tanstack/react-query";
import { useScanContext } from "@/lib/scan-context";
import { getWorkspaceSummary, getAbandonedProjects } from "@/lib/api-client";
import { formatBytes, formatNumber } from "@/lib/format";
import { Card, CardTitle, CardContent } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Badge } from "@/components/ui/badge";
import { FolderGit2, Package, Cpu } from "lucide-react";

export default function WorkspacesPage() {
  const { currentScanId } = useScanContext();

  const { data: summary } = useQuery({
    queryKey: ["workspace-summary", currentScanId],
    queryFn: () => getWorkspaceSummary(currentScanId!),
    enabled: !!currentScanId,
  });

  const { data: abandoned } = useQuery({
    queryKey: ["abandoned-projects", currentScanId],
    queryFn: () => getAbandonedProjects(currentScanId!),
    enabled: !!currentScanId,
  });

  const s = summary?.data;
  const ab = abandoned?.data;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold">Developer Workspace Optimizer</h1>
        <p className="mt-1 text-gray-500 dark:text-gray-400">
          Detect and clean up development environment waste
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          title="Workspaces Detected"
          value={formatNumber(s?.total_workspaces || 0)}
          icon={<FolderGit2 className="h-5 w-5" />}
        />
        <StatCard
          title="Recoverable Space"
          value={formatBytes(s?.total_recoverable_bytes || 0)}
          subtitle={s ? `${formatBytes(s.safe_recoverable_bytes)} safe to delete` : undefined}
          icon={<Package className="h-5 w-5" />}
        />
        <StatCard
          title="Abandoned Projects"
          value={formatNumber(s?.inactive_workspaces || 0)}
          icon={<Cpu className="h-5 w-5" />}
        />
      </div>

      {s && Object.keys(s.by_type).length > 0 && (
        <Card>
          <CardTitle>Workspace Types</CardTitle>
          <CardContent className="mt-4 space-y-2">
            {Object.entries(s.by_type)
              .sort(([, a], [, b]) => b.recoverable_bytes - a.recoverable_bytes)
              .map(([type, data]) => (
                <div key={type} className="flex items-center justify-between rounded-lg border p-3 dark:border-gray-800">
                  <div>
                    <span className="font-medium capitalize">{type}</span>
                    <span className="ml-2 text-sm text-gray-500">{data.count} workspace(s)</span>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-bold text-green-600">{formatBytes(data.recoverable_bytes)}</p>
                    <p className="text-xs text-gray-500">recoverable</p>
                  </div>
                </div>
              ))}
          </CardContent>
        </Card>
      )}

      {ab && ab.projects.length > 0 && (
        <Card>
          <CardTitle>Abandoned Projects ({ab.abandoned_count})</CardTitle>
          <CardContent className="mt-4 space-y-2">
            {ab.projects.slice(0, 15).map((p) => (
              <div key={p.path} className="flex items-center justify-between rounded-md border p-2 dark:border-gray-800">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{p.name}</p>
                  <p className="truncate text-xs text-gray-500">{p.path}</p>
                </div>
                <div className="ml-2 text-right">
                  <Badge variant="warning">{p.days_inactive ? `${p.days_inactive}d inactive` : "inactive"}</Badge>
                  <p className="mt-0.5 text-xs text-green-600">{formatBytes(p.recoverable_bytes)}</p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {!currentScanId && (
        <Card className="py-12 text-center">
          <p className="text-gray-500">Run a scan and workspace analysis to see results.</p>
        </Card>
      )}
    </div>
  );
}
