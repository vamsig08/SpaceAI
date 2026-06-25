"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useScanContext } from "@/lib/scan-context";
import { getDuplicateSummary, getDuplicateGroups, getDuplicateCleanupPaths, proposeCleanup } from "@/lib/api-client";
import { formatBytes, formatNumber } from "@/lib/format";
import { useToast } from "@/components/ui/toast";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Copy, Trash2, Shield, Sparkles } from "lucide-react";
import Link from "next/link";

export default function DuplicatesPage() {
  const { currentScanId } = useScanContext();
  const qc = useQueryClient();
  const { showToast } = useToast();

  const { data: summary } = useQuery({ queryKey: ["duplicate-summary", currentScanId], queryFn: () => getDuplicateSummary(currentScanId!), enabled: !!currentScanId });
  const { data: groups } = useQuery({ queryKey: ["duplicate-groups", currentScanId], queryFn: () => getDuplicateGroups(currentScanId!), enabled: !!currentScanId });

  const s = summary?.data;
  const groupList = groups?.data || [];

  const cleanAll = useMutation({
    mutationFn: async () => {
      const pathsResult = await getDuplicateCleanupPaths(currentScanId!);
      const paths = pathsResult.data.paths;
      if (paths.length === 0) throw new Error("No duplicate files to clean up");
      return proposeCleanup({ action_type: "trash", target_paths: paths, total_bytes: pathsResult.data.total_bytes });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cleanup-actions"] }); showToast({ type: "success", title: "Ready for cleanup", message: "Duplicate files queued. Go to Cleanup Center to confirm.", action: { label: "Review & Confirm", onClick: () => window.location.href = "/cleanup" } }); },
    onError: (e: Error) => showToast({ type: "error", title: "Could not prepare cleanup", message: e.message }),
  });

  if (!currentScanId) {
    return <EmptyState message="Scan your system first to detect duplicate files." />;
  }

  if (!s || s.total_groups === 0) {
    return (
      <div className="space-y-6">
        <PageHeader />
        <Card>
          <CardContent className="py-12 text-center">
            <Shield className="mx-auto h-10 w-10 text-green-500" />
            <h3 className="mt-3 text-lg font-semibold">No duplicates found</h3>
            <p className="mt-1 text-gray-500">Your files appear well-organized. No identical copies were detected.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader />

      {/* AI Insight */}
      <Card className="border-blue-200 bg-blue-50/50 dark:border-blue-900 dark:bg-blue-950/30">
        <CardContent>
          <div className="flex items-start gap-3">
            <Sparkles className="mt-0.5 h-5 w-5 text-blue-600 shrink-0" />
            <div>
              <p className="font-medium text-gray-900 dark:text-gray-100">
                I found {s.total_groups} {s.total_groups === 1 ? "set" : "sets"} of identical files taking up {formatBytes(s.total_wasted_bytes)} of unnecessary space.
              </p>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
                {s.total_duplicate_files} files share the same content — keeping one copy of each and removing the rest is completely safe.
                {s.top_extensions.length > 0 && ` Most duplicates are ${s.top_extensions.slice(0, 2).join(" and ")} files.`}
              </p>
              <div className="mt-3 flex items-center gap-3">
                <button
                  onClick={() => cleanAll.mutate()}
                  disabled={cleanAll.isPending}
                  className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" />
                  {cleanAll.isPending ? "Preparing..." : `Remove duplicates (recover ${formatBytes(s.total_wasted_bytes)})`}
                </button>
                <span className="text-xs text-gray-500">Files move to trash — you can undo anytime</span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Duplicate Groups */}
      <Card>
        <CardContent>
          <h2 className="text-lg font-semibold mb-4">Duplicate Sets ({s.total_groups})</h2>
          <div className="space-y-3">
            {groupList.slice(0, 20).map((g, i) => (
              <div key={g.id} className="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium">
                      {g.member_count} identical copies &middot; {formatBytes(g.file_size_bytes)} each
                    </p>
                    <p className="mt-0.5 text-sm text-gray-500">
                      {g.member_count - 1} {g.member_count - 1 === 1 ? "copy" : "copies"} can be removed safely
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold text-green-600">{formatBytes(g.wasted_bytes)}</p>
                    <p className="text-xs text-gray-500">recoverable</p>
                  </div>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <Badge variant="info">SHA-256 verified</Badge>
                  <span className="text-xs text-gray-400">100% confidence — cryptographically identical</span>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function PageHeader() {
  return (
    <div>
      <h1 className="text-3xl font-bold">Duplicate Files</h1>
      <p className="mt-1 text-gray-500">Identical files stored in multiple locations</p>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="space-y-6">
      <PageHeader />
      <Card><CardContent className="py-12 text-center"><Copy className="mx-auto h-10 w-10 text-gray-300" /><p className="mt-2 text-gray-500">{message}</p></CardContent></Card>
    </div>
  );
}
