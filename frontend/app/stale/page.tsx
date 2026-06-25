"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useScanContext } from "@/lib/scan-context";
import { getStaleSummary, getStaleFiles, getDevArtifacts, proposeCleanup } from "@/lib/api-client";
import { formatBytes, formatNumber } from "@/lib/format";
import { useToast } from "@/components/ui/toast";
import { Card, CardTitle, CardContent } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Badge } from "@/components/ui/badge";
import { Clock, Archive, Trash2 } from "lucide-react";

export default function StalePage() {
  const { currentScanId } = useScanContext();
  const { showToast } = useToast();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data: summary } = useQuery({ queryKey: ["stale-summary", currentScanId], queryFn: () => getStaleSummary(currentScanId!), enabled: !!currentScanId });
  const { data: files } = useQuery({ queryKey: ["stale-files", currentScanId], queryFn: () => getStaleFiles(currentScanId!, {}), enabled: !!currentScanId });
  const { data: artifacts } = useQuery({ queryKey: ["dev-artifacts", currentScanId], queryFn: () => getDevArtifacts(currentScanId!), enabled: !!currentScanId });

  const cleanup = useMutation({
    mutationFn: (paths: string[]) => proposeCleanup({ action_type: "trash", target_paths: paths, total_bytes: 0 }),
    onSuccess: () => { setSelected(new Set()); qc.invalidateQueries({ queryKey: ["cleanup-actions"] }); showToast({ type: "success", title: "Cleanup proposed", message: "Go to Cleanup Center to approve and execute.", action: { label: "Cleanup Center", onClick: () => window.location.href = "/cleanup" } }); },
    onError: (e: Error) => showToast({ type: "error", title: "Failed", message: e.message }),
  });

  const cls = summary?.data?.classification || {};
  const fileList = files?.data || [];
  const artData = artifacts?.data;

  const toggle = (path: string) => { const n = new Set(selected); if (n.has(path)) n.delete(path); else n.add(path); setSelected(n); };
  const selectAll = () => { if (selected.size === fileList.length) setSelected(new Set()); else setSelected(new Set(fileList.map(f => f.path))); };

  return (
    <div className="space-y-8">
      <div><h1 className="text-3xl font-bold">Stale File Analysis</h1><p className="mt-1 text-gray-500 dark:text-gray-400">Files that haven&apos;t been modified in months</p></div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard title="Active" value={formatNumber(cls.active?.count || 0)} subtitle={cls.active ? formatBytes(cls.active.bytes) : "—"} icon={<Clock className="h-5 w-5" />} />
        <StatCard title="Aging" value={formatNumber(cls.aging?.count || 0)} subtitle={cls.aging ? formatBytes(cls.aging.bytes) : "—"} />
        <StatCard title="Stale" value={formatNumber(cls.stale?.count || 0)} subtitle={cls.stale ? formatBytes(cls.stale.bytes) : "—"} />
        <StatCard title="Archive Candidates" value={formatNumber(cls.archive_candidate?.count || 0)} subtitle={cls.archive_candidate ? formatBytes(cls.archive_candidate.bytes) : "—"} icon={<Archive className="h-5 w-5" />} />
      </div>

      {summary?.data && summary.data.recoverable_bytes > 0 && (
        <Card><CardTitle>Recoverable Space</CardTitle><CardContent className="mt-2"><p className="text-3xl font-bold text-green-600">{formatBytes(summary.data.recoverable_bytes)}</p><p className="mt-1 text-sm text-gray-500">From {formatNumber(summary.data.total_stale_files)} stale files</p></CardContent></Card>
      )}

      {fileList.length > 0 && (
        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>Stale Files ({fileList.length})</CardTitle>
            <div className="flex items-center gap-2">
              <button onClick={selectAll} className="text-xs text-blue-600 hover:underline">{selected.size === fileList.length ? "Deselect all" : "Select all"}</button>
              {selected.size > 0 && (
                <button onClick={() => cleanup.mutate(Array.from(selected))} disabled={cleanup.isPending} className="flex items-center gap-1 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50">
                  <Trash2 className="h-3 w-3" /> Move {selected.size} to Trash
                </button>
              )}
            </div>
          </div>
          <CardContent className="mt-4 space-y-1 max-h-96 overflow-y-auto">
            {fileList.slice(0, 50).map((f) => (
              <label key={f.id} className="flex items-center gap-3 rounded-md border p-2 hover:bg-gray-50 cursor-pointer dark:border-gray-800 dark:hover:bg-gray-900">
                <input type="checkbox" checked={selected.has(f.path)} onChange={() => toggle(f.path)} className="h-4 w-4 rounded border-gray-300" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{f.filename}</p>
                  <p className="truncate text-xs text-gray-500">{f.path}</p>
                </div>
                <div className="flex items-center gap-2 text-xs shrink-0">
                  <Badge variant={f.risk_level === "low" ? "success" : f.risk_level === "medium" ? "warning" : "danger"}>{f.risk_level || "—"}</Badge>
                  <span className="text-gray-500">{formatBytes(f.size_bytes)}</span>
                </div>
              </label>
            ))}
          </CardContent>
        </Card>
      )}

      {artData && artData.total_artifact_files > 0 && (
        <Card><CardTitle>Developer Artifacts</CardTitle><CardContent className="mt-4 space-y-2">
          {Object.entries(artData.artifacts).map(([t, d]) => (<div key={t} className="flex items-center justify-between rounded-md border p-2 dark:border-gray-800"><span className="text-sm font-medium capitalize">{t.replace(/_/g, " ")}</span><span className="text-sm">{formatNumber(d.count)} files &middot; {formatBytes(d.bytes)}</span></div>))}
          <p className="mt-2 text-sm text-green-600 font-medium">Total recoverable: {formatBytes(artData.total_recoverable_bytes)}</p>
        </CardContent></Card>
      )}

      {!currentScanId && <Card className="py-12 text-center"><p className="text-gray-500">Run a scan to analyze stale files.</p></Card>}
    </div>
  );
}
