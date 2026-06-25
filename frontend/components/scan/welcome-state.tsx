"use client";

import { useState } from "react";
import { useScanContext } from "@/lib/scan-context";
import { FolderSearch, ArrowRight, Search, BarChart3, Trash2, Loader2 } from "lucide-react";

export function WelcomeState() {
  const { startScan } = useScanContext();
  const [path, setPath] = useState(getDefaultPath());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleStart = async () => {
    if (!path.trim()) return;
    setError(null);
    setLoading(true);
    try {
      await startScan(path.trim());
    } catch (e: any) {
      setError(e.message || "Failed to start scan");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-[70vh] flex-col items-center justify-center text-center">
      {/* Logo and title */}
      <div className="mb-8">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 to-purple-600 shadow-lg">
          <FolderSearch className="h-8 w-8 text-white" />
        </div>
        <h1 className="text-4xl font-bold text-gray-900 dark:text-white">Welcome to SpaceAI</h1>
        <p className="mx-auto mt-3 max-w-lg text-lg text-gray-500 dark:text-gray-400">
          AI-powered storage optimization that helps you understand, predict, and reclaim disk space.
        </p>
      </div>

      {/* Scan input */}
      <div className="w-full max-w-lg">
        <label className="mb-2 block text-left text-sm font-medium text-gray-700 dark:text-gray-300">
          Directory to scan
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/Users/you or /home/dev"
            className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-3 text-base focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            onKeyDown={(e) => e.key === "Enter" && handleStart()}
            disabled={loading}
          />
          <button
            onClick={handleStart}
            disabled={loading || !path.trim()}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-6 py-3 text-base font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <ArrowRight className="h-5 w-5" />}
            Start Scan
          </button>
        </div>
        {error && <p className="mt-2 text-left text-sm text-red-500">{error}</p>}
      </div>

      {/* 3-step explanation */}
      <div className="mt-12 grid w-full max-w-2xl grid-cols-3 gap-6">
        <StepCard
          step={1}
          icon={<Search className="h-6 w-6" />}
          title="Scan"
          description="Discover all files and folders on your disk"
        />
        <StepCard
          step={2}
          icon={<BarChart3 className="h-6 w-6" />}
          title="Analyze"
          description="Find duplicates, stale files, and waste"
        />
        <StepCard
          step={3}
          icon={<Trash2 className="h-6 w-6" />}
          title="Clean Up"
          description="Safely reclaim space with full rollback"
        />
      </div>
    </div>
  );
}

function StepCard({
  step,
  icon,
  title,
  description,
}: {
  step: number;
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-blue-100 text-blue-600 dark:bg-blue-950 dark:text-blue-400">
        {icon}
      </div>
      <div className="mb-1 text-xs font-medium uppercase text-gray-400">Step {step}</div>
      <h3 className="text-base font-semibold">{title}</h3>
      <p className="mt-1 text-sm text-gray-500">{description}</p>
    </div>
  );
}

function getDefaultPath(): string {
  // Best-effort default based on common patterns
  if (typeof window !== "undefined") {
    // Check if we can detect the platform from user agent
    const ua = navigator.userAgent.toLowerCase();
    if (ua.includes("mac")) return "/Users";
    if (ua.includes("win")) return "C:\\Users";
    return "/home";
  }
  return "/home";
}
