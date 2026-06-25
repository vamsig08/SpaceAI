/**
 * Formatting utilities for human-readable display.
 */

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatNumber(n: number): string {
  return n.toLocaleString();
}

export function formatDate(isoDate: string | null): string {
  if (!isoDate) return "—";
  return new Date(isoDate).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatRelativeDate(isoDate: string | null): string {
  if (!isoDate) return "—";
  const now = Date.now();
  const then = new Date(isoDate).getTime();
  const diffDays = Math.floor((now - then) / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 30) return `${diffDays} days ago`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
  return `${Math.floor(diffDays / 365)} years ago`;
}

export function formatPercentage(value: number, total: number): string {
  if (total === 0) return "0%";
  return `${((value / total) * 100).toFixed(1)}%`;
}

export function priorityColor(priority: string): string {
  switch (priority) {
    case "critical": return "text-red-500";
    case "high": return "text-orange-500";
    case "medium": return "text-yellow-500";
    case "low": return "text-green-500";
    default: return "text-gray-500";
  }
}

export function riskColor(risk: string): string {
  switch (risk) {
    case "high": return "text-red-500 bg-red-50 dark:bg-red-950";
    case "medium": return "text-yellow-600 bg-yellow-50 dark:bg-yellow-950";
    case "low": return "text-green-600 bg-green-50 dark:bg-green-950";
    default: return "text-gray-500";
  }
}

export function trendLabel(trend: string): string {
  switch (trend) {
    case "stable": return "Stable";
    case "slow_growth": return "Slow Growth";
    case "moderate_growth": return "Moderate Growth";
    case "rapid_growth": return "Rapid Growth";
    case "critical_growth": return "Critical Growth";
    default: return trend;
  }
}
