"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useScanContext } from "@/lib/scan-context";
import { getRecommendations, updateRecommendation } from "@/lib/api-client";
import { formatBytes } from "@/lib/format";
import { useToast } from "@/components/ui/toast";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sparkles, CheckCircle, XCircle, Lightbulb, Shield } from "lucide-react";

export default function RecommendationsPage() {
  const { currentScanId } = useScanContext();
  const qc = useQueryClient();
  const { showToast } = useToast();

  const { data } = useQuery({
    queryKey: ["recommendations", currentScanId],
    queryFn: () => getRecommendations(currentScanId!),
    enabled: !!currentScanId,
  });

  const accept = useMutation({
    mutationFn: (id: string) => updateRecommendation(id, "accepted"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["recommendations"] }); showToast({ type: "success", title: "Got it", message: "I'll prepare this for cleanup." }); },
    onError: (e: Error) => showToast({ type: "error", title: "Failed", message: e.message }),
  });

  const dismiss = useMutation({
    mutationFn: (id: string) => updateRecommendation(id, "dismissed", "User dismissed"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["recommendations"] }); showToast({ type: "info", title: "Noted — I'll skip this one" }); },
    onError: (e: Error) => showToast({ type: "error", title: "Failed", message: e.message }),
  });

  const recs = data?.data || [];
  const pending = recs.filter(r => r.status === "pending");
  const resolved = recs.filter(r => r.status !== "pending");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">My Suggestions</h1>
        <p className="mt-1 text-gray-500">Personalized recommendations based on your storage analysis</p>
      </div>

      {pending.length > 0 ? (
        <div className="space-y-4">
          {pending.map((rec) => (
            <Card key={rec.id} className="overflow-hidden">
              <CardContent>
                <div className="flex items-start gap-4">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-100 dark:bg-blue-950">
                    <Sparkles className="h-4 w-4 text-blue-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <Badge variant={rec.priority === "high" ? "warning" : rec.priority === "critical" ? "danger" : "default"}>
                        {rec.priority} priority
                      </Badge>
                      <span className="text-xs text-gray-500">{Math.round(rec.confidence * 100)}% confidence</span>
                    </div>
                    <h3 className="font-semibold text-gray-900 dark:text-gray-100">{rec.title}</h3>
                    <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">{rec.description}</p>
                    {rec.explanation && (
                      <p className="mt-2 text-xs text-gray-500 italic border-l-2 border-gray-200 pl-3 dark:border-gray-700">{rec.explanation}</p>
                    )}
                    <div className="mt-4 flex items-center gap-3">
                      <button
                        onClick={() => accept.mutate(rec.id)}
                        disabled={accept.isPending}
                        className="flex items-center gap-1.5 rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
                      >
                        <CheckCircle className="h-4 w-4" /> Yes, clean this up
                      </button>
                      <button
                        onClick={() => dismiss.mutate(rec.id)}
                        disabled={dismiss.isPending}
                        className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-400"
                      >
                        <XCircle className="h-4 w-4" /> Skip
                      </button>
                      <span className="ml-auto text-lg font-bold text-green-600">{formatBytes(rec.recoverable_bytes)}</span>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : currentScanId ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Shield className="mx-auto h-10 w-10 text-green-500" />
            <h3 className="mt-3 text-lg font-semibold">Everything looks good</h3>
            <p className="mt-1 text-gray-500 max-w-md mx-auto">
              I didn't find any significant optimization opportunities. Your storage is well-managed.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="py-12 text-center">
            <Lightbulb className="mx-auto h-10 w-10 text-yellow-400" />
            <h3 className="mt-3 text-lg font-semibold">Scan your system first</h3>
            <p className="mt-1 text-gray-500">I'll analyze your files and provide personalized cleanup suggestions.</p>
          </CardContent>
        </Card>
      )}

      {resolved.length > 0 && (
        <div className="mt-8">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-3">Resolved</h2>
          <div className="space-y-2 opacity-60">
            {resolved.map((rec) => (
              <div key={rec.id} className="flex items-center justify-between rounded-lg border p-3 dark:border-gray-800">
                <span className="text-sm">{rec.title}</span>
                <Badge variant={rec.status === "accepted" ? "success" : "default"}>{rec.status}</Badge>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
