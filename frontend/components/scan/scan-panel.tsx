"use client";

import { useState } from "react";
import { useScanContext } from "@/lib/scan-context";
import { formatBytes, formatNumber } from "@/lib/format";
import { Card, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { FolderSearch, Play, XCircle, CheckCircle2, Loader2 } from "lucide-react";

export function ScanPanel() {
  const { isScanning, progress, currentScan, startScan, cancelScan } = useScanContext();
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleStart = async () => {
    if (!path.trim()) {
      setError("Please enter a directory path");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await startScan(path.trim());
      setPath("");
    } catch (e: any) {
      setError(e.message || "Failed to start scan");
    } finally {
      setLoading(false);
    }
  };

  if (isScanning && progress) {
    return (
      <Card className="border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
            <div>
              <p className="font-medium text-blue-900 dark:text-blue-100">Scanning in progress</p>
              <p className="mt-0.5 text-sm text-blue-700 dark:text-blue-300 truncate max-w-md">
                {progress.current_directory}
              </p>
            </div>
          </div>
          <button
            onClick={cancelScan}
            className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 dark:border-red-700 dark:text-red-400 dark:hover:bg-red-950"
          >
            Cancel
          </button>
        </div>

        <div className="mt-4 grid grid-cols-4 gap-4 text-center">
          <div>
            <p className="text-2xl font-bold text-blue-900 dark:text-blue-100">{formatNumber(progress.files_scanned)}</p>
            <p className="text-xs text-blue-600 dark:text-blue-400">Files</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-blue-900 dark:text-blue-100">{formatNumber(progress.dirs_scanned)}</p>
            <p className="text-xs text-blue-600 dark:text-blue-400">Dirs</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-blue-900 dark:text-blue-100">
              {progress.files_per_second > 0 ? formatNumber(Math.round(progress.files_per_second)) : "—"}
            </p>
            <p className="text-xs text-blue-600 dark:text-blue-400">Files/sec</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-blue-900 dark:text-blue-100">
              {progress.errors_skipped > 0 ? formatNumber(progress.errors_skipped) : "0"}
            </p>
            <p className="text-xs text-blue-600 dark:text-blue-400">Errors</p>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-center gap-3">
        <FolderSearch className="h-5 w-5 text-gray-500" />
        <CardTitle>Start a Scan</CardTitle>
      </div>
      <CardContent className="mt-4">
        <div className="flex gap-2">
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/Users/you or /home/dev"
            className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            onKeyDown={(e) => e.key === "Enter" && handleStart()}
            disabled={loading}
          />
          <button
            onClick={handleStart}
            disabled={loading || !path.trim()}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Scan
          </button>
        </div>
        {error && (
          <p className="mt-2 text-sm text-red-500">{error}</p>
        )}
        {currentScan && currentScan.status === "completed" && (
          <div className="mt-3 flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
            <CheckCircle2 className="h-4 w-4" />
            Last scan: {formatNumber(currentScan.total_files)} files, {formatBytes(currentScan.total_size_bytes)}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
