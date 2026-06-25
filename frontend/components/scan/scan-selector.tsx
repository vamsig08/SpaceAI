"use client";

import { useScanContext } from "@/lib/scan-context";
import { formatBytes, formatRelativeDate } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { ChevronDown } from "lucide-react";
import { useState, useRef, useEffect } from "react";

export function ScanSelector() {
  const { scans, currentScanId, selectScan } = useScanContext();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const current = scans.find((s) => s.id === currentScanId);
  if (scans.length === 0) return null;

  const statusVariant = (status: string) => {
    switch (status) {
      case "completed": return "success" as const;
      case "running": return "info" as const;
      case "failed": return "danger" as const;
      default: return "default" as const;
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-800"
      >
        <span className="max-w-[200px] truncate">{current?.root_path || "Select scan"}</span>
        <Badge variant={statusVariant(current?.status || "unknown")} className="text-[10px]">
          {current?.status || "—"}
        </Badge>
        <ChevronDown className="h-3 w-3" />
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-80 rounded-lg border border-gray-200 bg-white shadow-lg dark:border-gray-700 dark:bg-gray-900">
          <div className="max-h-64 overflow-y-auto p-1">
            {scans.map((scan) => (
              <button
                key={scan.id}
                onClick={() => { selectScan(scan.id); setOpen(false); }}
                className={`w-full rounded-md px-3 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800 ${
                  scan.id === currentScanId ? "bg-blue-50 dark:bg-blue-950" : ""
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="truncate font-medium">{scan.root_path}</span>
                  <Badge variant={statusVariant(scan.status)} className="text-[10px]">{scan.status}</Badge>
                </div>
                <div className="mt-0.5 text-xs text-gray-500">
                  {scan.total_files > 0 ? `${scan.total_files.toLocaleString()} files` : ""} &middot; {formatRelativeDate(scan.created_at)}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
