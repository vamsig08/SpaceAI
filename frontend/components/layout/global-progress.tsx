"use client";

import { useScanContext } from "@/lib/scan-context";
import { formatNumber } from "@/lib/format";
import { Loader2, CheckCircle2 } from "lucide-react";

export function GlobalProgress() {
  const { isScanning, progress, currentScan } = useScanContext();

  if (!isScanning || !progress) return null;

  return (
    <div className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 dark:border-blue-800 dark:bg-blue-950">
      <Loader2 className="h-4 w-4 animate-spin text-blue-600 dark:text-blue-400" />
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium text-blue-700 dark:text-blue-300">Scanning</span>
        <span className="text-blue-600 dark:text-blue-400">
          {formatNumber(progress.files_scanned)} files
        </span>
        {progress.files_per_second > 0 && (
          <span className="text-blue-500 dark:text-blue-500">
            ({formatNumber(Math.round(progress.files_per_second))}/s)
          </span>
        )}
      </div>
    </div>
  );
}
