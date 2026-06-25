"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getCleanupActions, getAuditLog, approveCleanup, executeCleanup, rollbackCleanup } from "@/lib/api-client";
import { formatBytes, formatDate } from "@/lib/format";
import { useToast } from "@/components/ui/toast";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Trash2, RotateCcw, Shield, Play, CheckCircle, Undo2, FileText } from "lucide-react";

export default function CleanupPage() {
  const qc = useQueryClient();
  const { showToast } = useToast();
  const { data: actions } = useQuery({ queryKey: ["cleanup-actions"], queryFn: () => getCleanupActions() });
  const { data: audit } = useQuery({ queryKey: ["audit-log"], queryFn: () => getAuditLog() });

  const actionList = actions?.data || [];
  const auditList = audit?.data || [];

  const approve = useMutation({
    mutationFn: approveCleanup,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cleanup-actions"] }); showToast({ type: "success", title: "Approved", message: "Click Execute when ready. Files will move to trash." }); },
    onError: (e: Error) => showToast({ type: "error", title: "Could not approve", message: e.message }),
  });

  const execute = useMutation({
    mutationFn: executeCleanup,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cleanup-actions"] }); qc.invalidateQueries({ queryKey: ["audit-log"] }); qc.invalidateQueries({ queryKey: ["overview"] }); showToast({ type: "success", title: "Done — space recovered!", message: "Files moved to trash. You can undo this anytime." }); },
    onError: (e: Error) => showToast({ type: "error", title: "Cleanup failed", message: e.message }),
  });

  const rollback = useMutation({
    mutationFn: rollbackCleanup,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cleanup-actions"] }); qc.invalidateQueries({ queryKey: ["audit-log"] }); qc.invalidateQueries({ queryKey: ["overview"] }); showToast({ type: "success", title: "Restored", message: "All files returned to their original locations." }); },
    onError: (e: Error) => showToast({ type: "error", title: "Could not restore", message: e.message }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Cleanup Center</h1>
        <p className="mt-1 text-gray-500">Review, execute, and undo storage cleanup operations</p>
      </div>

      {/* Safety Promise */}
      <div className="flex items-center gap-6 rounded-xl border border-green-200 bg-green-50/50 p-4 dark:border-green-900 dark:bg-green-950/20">
        <Shield className="h-8 w-8 text-green-600 shrink-0" />
        <div className="text-sm text-gray-700 dark:text-gray-300">
          <span className="font-medium">Your safety promise:</span> Nothing is ever permanently deleted.
          Files move to <code className="text-xs bg-green-100 dark:bg-green-900 px-1 py-0.5 rounded">~/.spaceai/trash/</code> and can be restored at any time.
        </div>
      </div>

      {/* Actions */}
      {actionList.length > 0 ? (
        <div className="space-y-3">
          {actionList.map((a) => (
            <Card key={a.id}>
              <CardContent>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <StatusBadge status={a.status} />
                      <span className="text-sm text-gray-500">{a.target_count} files &middot; {formatBytes(a.total_bytes)}</span>
                    </div>
                    <p className="text-sm font-medium">
                      {describeAction(a)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {(a.status === "proposed" || a.status === "dry_run_complete") && (
                      <button onClick={() => approve.mutate(a.id)} disabled={approve.isPending}
                        className="flex items-center gap-1.5 rounded-lg bg-green-600 px-3 py-2 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50">
                        <CheckCircle className="h-3.5 w-3.5" /> Approve
                      </button>
                    )}
                    {a.status === "approved" && (
                      <button onClick={() => execute.mutate(a.id)} disabled={execute.isPending}
                        className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50">
                        <Play className="h-3.5 w-3.5" /> Execute — Move to Trash
                      </button>
                    )}
                    {(a.status === "completed" || a.status === "failed") && (
                      <button onClick={() => rollback.mutate(a.id)} disabled={rollback.isPending}
                        className="flex items-center gap-1.5 rounded-lg border border-orange-300 px-3 py-2 text-xs font-medium text-orange-700 hover:bg-orange-50 disabled:opacity-50 dark:border-orange-800 dark:text-orange-400">
                        <Undo2 className="h-3.5 w-3.5" /> Undo — Restore Files
                      </button>
                    )}
                    {a.bytes_recovered > 0 && (
                      <span className="text-sm font-bold text-green-600">{formatBytes(a.bytes_recovered)} freed</span>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="py-12 text-center">
            <Trash2 className="mx-auto h-10 w-10 text-gray-300" />
            <h3 className="mt-3 text-lg font-semibold">No pending cleanups</h3>
            <p className="mt-1 text-gray-500">Select files from Duplicates or Stale Files to create a cleanup plan.</p>
          </CardContent>
        </Card>
      )}

      {/* Audit History */}
      {auditList.length > 0 && (
        <Card>
          <CardContent>
            <div className="flex items-center gap-2 mb-3">
              <FileText className="h-4 w-4 text-gray-500" />
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Activity Log</h2>
            </div>
            <div className="space-y-2">
              {auditList.slice(0, 8).map((e) => (
                <div key={e.id} className="flex items-center justify-between text-sm border-b border-gray-100 pb-2 last:border-0 dark:border-gray-800">
                  <span className="text-gray-600 dark:text-gray-400">{humanizeAction(e.action)} {e.description ? `— ${e.description}` : ""}</span>
                  <span className="text-xs text-gray-400 shrink-0">{formatDate(e.created_at)}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "proposed": return <Badge variant="info">Waiting for approval</Badge>;
    case "dry_run_complete": return <Badge variant="info">Validated</Badge>;
    case "approved": return <Badge variant="warning">Ready to execute</Badge>;
    case "completed": return <Badge variant="success">Completed</Badge>;
    case "failed": return <Badge variant="danger">Failed</Badge>;
    case "rolled_back": return <Badge variant="default">Restored</Badge>;
    default: return <Badge>{status}</Badge>;
  }
}

function describeAction(a: any): string {
  if (a.status === "completed") return `Moved ${a.target_count} files to trash, recovered ${formatBytes(a.bytes_recovered)}`;
  if (a.status === "rolled_back") return `Files restored to original locations`;
  if (a.status === "approved") return `${a.target_count} files ready to move to trash`;
  return `${a.target_count} files identified for cleanup`;
}

function humanizeAction(action: string): string {
  return action.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase());
}
