"use client";

import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import { formatBytes, formatNumber } from "@/lib/format";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

interface Scan {
  id: string;
  root_path: string;
  status: string;
  scan_type: string;
  started_at: string | null;
  completed_at: string | null;
  total_files: number;
  total_dirs: number;
  total_size_bytes: number;
  files_per_second: number | null;
  error_message: string | null;
  platform: string | null;
  created_at: string;
}

interface ScanProgress {
  files_scanned: number;
  dirs_scanned: number;
  current_directory: string;
  total_bytes_scanned: number;
  files_per_second: number;
  errors_skipped: number;
  eta_seconds: number | null;
}

interface ScanContextValue {
  currentScanId: string | null;
  currentScan: Scan | null;
  scans: Scan[];
  isScanning: boolean;
  progress: ScanProgress | null;
  selectScan: (scanId: string) => void;
  startScan: (rootPath: string, scanType?: string, exclusions?: string[]) => Promise<string>;
  cancelScan: () => Promise<void>;
  refetchScans: () => void;
}

const ScanContext = createContext<ScanContextValue | null>(null);

export function useScanContext(): ScanContextValue {
  const ctx = useContext(ScanContext);
  if (!ctx) throw new Error("useScanContext must be used within ScanProvider");
  return ctx;
}

export function ScanProvider({ children }: { children: ReactNode }) {
  const [currentScanId, setCurrentScanId] = useState<string | null>(null);
  const [progress, setProgress] = useState<ScanProgress | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const { showToast } = useToast();

  // Fetch scan list
  const { data: scansData, refetch: refetchScans } = useQuery({
    queryKey: ["scans"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/scans?page_size=50`);
      if (!res.ok) return { data: [] };
      return res.json();
    },
    refetchInterval: isScanning ? 2000 : false,
  });

  const scans: Scan[] = scansData?.data || [];

  // Auto-select latest completed scan if none selected
  useEffect(() => {
    if (!currentScanId && scans.length > 0) {
      const completed = scans.find((s) => s.status === "completed");
      if (completed) setCurrentScanId(completed.id);
    }
  }, [scans, currentScanId]);

  // Current scan object
  const currentScan = scans.find((s) => s.id === currentScanId) || null;

  // SSE progress subscription
  useEffect(() => {
    if (!isScanning || !currentScanId) return;

    const evtSource = new EventSource(`${API_BASE}/scans/${currentScanId}/progress`);

    evtSource.addEventListener("progress", (e) => {
      const data = JSON.parse(e.data);
      setProgress(data);
    });

    evtSource.addEventListener("completed", (e) => {
      const data = JSON.parse(e.data);
      setIsScanning(false);
      setProgress(null);
      refetchScans();
      showToast({
        type: "success",
        title: "Scan Complete",
        message: `${formatNumber(data.total_files || 0)} files scanned in ${Math.round(data.duration_seconds || 0)}s`,
        action: { label: "View Analytics", onClick: () => window.location.href = "/analytics" },
      });
    });

    evtSource.addEventListener("failed", (e) => {
      setIsScanning(false);
      setProgress(null);
      refetchScans();
      showToast({
        type: "error",
        title: "Scan Failed",
        message: "An error occurred during scanning. Check the scan details for more info.",
      });
    });

    evtSource.addEventListener("cancelled", () => {
      setIsScanning(false);
      setProgress(null);
      refetchScans();
    });

    evtSource.onerror = () => {
      evtSource.close();
      setIsScanning(false);
      setProgress(null);
    };

    return () => evtSource.close();
  }, [isScanning, currentScanId]);

  const selectScan = useCallback((scanId: string) => {
    setCurrentScanId(scanId);
  }, []);

  const startScan = useCallback(async (rootPath: string, scanType = "full", exclusions: string[] = []): Promise<string> => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/scans`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root_path: rootPath, scan_type: scanType, exclusions }),
      });
    } catch (e) {
      throw new Error("Cannot connect to SpaceAI server. Is the backend running?");
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: { message: `Server error (${res.status})` } }));
      throw new Error(err.error?.message || `HTTP ${res.status}`);
    }
    const data = await res.json();
    const scanId = data.data.id;
    setCurrentScanId(scanId);
    setIsScanning(true);
    setProgress(null);
    refetchScans();
    return scanId;
  }, [refetchScans]);

  const cancelScan = useCallback(async () => {
    if (!currentScanId) return;
    try {
      await fetch(`${API_BASE}/scans/${currentScanId}`, { method: "DELETE" });
      showToast({ type: "info", title: "Scan cancelled" });
    } catch (e) {
      showToast({ type: "error", title: "Cancel failed", message: "Could not reach the server." });
    }
    setIsScanning(false);
    setProgress(null);
    refetchScans();
  }, [currentScanId, refetchScans, showToast]);

  return (
    <ScanContext.Provider
      value={{
        currentScanId,
        currentScan,
        scans,
        isScanning,
        progress,
        selectScan,
        startScan,
        cancelScan,
        refetchScans,
      }}
    >
      {children}
    </ScanContext.Provider>
  );
}
