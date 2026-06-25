"use client";

import { useQuery } from "@tanstack/react-query";
import { getOverview, getExhaustion, getGrowthRate } from "@/lib/api-client";
import { formatBytes, formatNumber } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScanPanel } from "@/components/scan/scan-panel";
import { WelcomeState } from "@/components/scan/welcome-state";
import { useScanContext } from "@/lib/scan-context";
import { HardDrive, AlertTriangle, CheckCircle2, ArrowRight, Sparkles } from "lucide-react";
import Link from "next/link";

export default function OverviewPage() {
  const { currentScanId, scans } = useScanContext();

  const { data: overview, isLoading } = useQuery({ queryKey: ["overview"], queryFn: getOverview });
  const { data: exhaustion } = useQuery({ queryKey: ["exhaustion"], queryFn: getExhaustion });
  const { data: growth } = useQuery({ queryKey: ["growth-rate"], queryFn: getGrowthRate });

  if (!isLoading && scans.length === 0) return <WelcomeState />;
  if (isLoading) return <DashboardSkeleton />;

  const o = overview?.data;
  const e = exhaustion?.data;
  const g = growth?.data;

  const hasAnalysis = (o?.duplicate_waste || 0) > 0 || (o?.stale_files_size || 0) > 0 || (o?.recovery_opportunities || 0) > 0;
  const freePercent = o?.total_storage ? Math.round((o.free_storage / o.total_storage) * 100) : 0;
  const usedPercent = 100 - freePercent;
  const isLow = freePercent < 10;
  const isCritical = freePercent < 5;

  return (
    <div className="space-y-6">
      <ScanPanel />

      {/* AI Briefing Header */}
      <div className="rounded-xl border border-gray-200 bg-gradient-to-br from-gray-50 to-white p-6 dark:border-gray-800 dark:from-gray-900 dark:to-gray-950">
        <div className="flex items-start gap-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-100 dark:bg-blue-950">
            <Sparkles className="h-5 w-5 text-blue-600 dark:text-blue-400" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">{getHeadline(o, hasAnalysis, isCritical, isLow)}</h1>
            <p className="mt-2 text-gray-600 dark:text-gray-400 leading-relaxed">
              {getSummary(o, hasAnalysis, freePercent)}
            </p>
          </div>
        </div>
      </div>

      {/* Storage Health Bar */}
      <Card>
        <CardContent>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <HardDrive className="h-4 w-4 text-gray-500" />
              <span className="text-sm font-medium">Storage Health</span>
            </div>
            <span className="text-sm text-gray-500">
              {formatBytes(o?.free_storage || 0)} available of {formatBytes(o?.total_storage || 0)}
            </span>
          </div>
          <div className="h-4 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
            <div
              className={`h-full rounded-full transition-all ${isCritical ? "bg-red-500" : isLow ? "bg-orange-500" : "bg-blue-500"}`}
              style={{ width: `${usedPercent}%` }}
            />
          </div>
          <div className="mt-2 flex justify-between text-xs text-gray-500">
            <span>{usedPercent}% used</span>
            {isLow && (
              <span className="flex items-center gap-1 text-orange-600 font-medium">
                <AlertTriangle className="h-3 w-3" />
                {isCritical ? "Critical — take action now" : "Running low"}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* What I Found / What You Can Do */}
      {hasAnalysis ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {/* Findings */}
          <Card>
            <CardContent>
              <h2 className="text-lg font-semibold mb-4">What I Found</h2>
              <div className="space-y-3">
                {(o?.recovery_opportunities || 0) > 0 && (
                  <Finding
                    title={`${formatBytes(o!.recovery_opportunities)} can be safely recovered`}
                    description="Based on duplicate files, stale content, and developer artifacts"
                    variant="success"
                    href="/recommendations"
                  />
                )}
                {(o?.duplicate_waste || 0) > 0 && (
                  <Finding
                    title={`${formatBytes(o!.duplicate_waste)} in duplicate files`}
                    description="Identical files stored in multiple locations"
                    variant="info"
                    href="/duplicates"
                  />
                )}
                {(o?.stale_files_size || 0) > 0 && (
                  <Finding
                    title={`${formatBytes(o!.stale_files_size)} in files you haven't used`}
                    description="Files not modified in over 6 months"
                    variant="warning"
                    href="/stale"
                  />
                )}
              </div>
            </CardContent>
          </Card>

          {/* Quick Actions */}
          <Card>
            <CardContent>
              <h2 className="text-lg font-semibold mb-4">Suggested Actions</h2>
              <div className="space-y-2">
                <ActionLink href="/duplicates" label="Review duplicate files" description="Remove redundant copies safely" />
                <ActionLink href="/stale" label="Clean up old files" description="Archive or remove unused content" />
                <ActionLink href="/workspaces" label="Optimize dev workspaces" description="node_modules, .venv, build artifacts" />
                <ActionLink href="/cleanup" label="View cleanup queue" description="Approve and execute pending cleanups" />
              </div>
            </CardContent>
          </Card>
        </div>
      ) : (
        <Card>
          <CardContent className="text-center py-8">
            <CheckCircle2 className="mx-auto h-10 w-10 text-green-500" />
            <h3 className="mt-3 text-lg font-semibold">Scan Complete</h3>
            <p className="mt-1 text-gray-500 max-w-md mx-auto">
              I scanned {formatNumber(o?.file_count || 0)} files across {formatNumber(o?.dir_count || 0)} folders.
              Analysis is running in the background — results will appear shortly.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Growth insight */}
      {g && g.weekly_growth_bytes > 0 && (
        <Card>
          <CardContent>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              <span className="font-medium text-gray-900 dark:text-gray-100">Growth trend: </span>
              Your storage is growing at about {formatBytes(g.weekly_growth_bytes)} per week.
              {e?.days_until_full && e.days_until_full < 365
                ? ` At this rate, you'll run out of space in about ${e.days_until_full} days.`
                : " This is within normal range."
              }
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Finding({ title, description, variant, href }: { title: string; description: string; variant: "success" | "info" | "warning"; href: string }) {
  const colors = {
    success: "border-l-green-500 bg-green-50/50 dark:bg-green-950/20",
    info: "border-l-blue-500 bg-blue-50/50 dark:bg-blue-950/20",
    warning: "border-l-orange-500 bg-orange-50/50 dark:bg-orange-950/20",
  };
  return (
    <Link href={href} className={`block rounded-lg border-l-4 p-3 ${colors[variant]} hover:opacity-80 transition-opacity`}>
      <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{title}</p>
      <p className="mt-0.5 text-xs text-gray-500">{description}</p>
    </Link>
  );
}

function ActionLink({ href, label, description }: { href: string; label: string; description: string }) {
  return (
    <Link href={href} className="flex items-center justify-between rounded-lg border border-gray-200 p-3 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900 transition-colors">
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-gray-500">{description}</p>
      </div>
      <ArrowRight className="h-4 w-4 text-gray-400" />
    </Link>
  );
}

function getHeadline(o: any, hasAnalysis: boolean, isCritical: boolean, isLow: boolean): string {
  if (isCritical) return "Your disk is almost full.";
  if (isLow) return "You're running low on space.";
  if (hasAnalysis && (o?.recovery_opportunities || 0) > 0) return `I can help you recover ${formatBytes(o.recovery_opportunities)}.`;
  if (hasAnalysis) return "Your storage looks healthy.";
  return "Here's what I found on your system.";
}

function getSummary(o: any, hasAnalysis: boolean, freePercent: number): string {
  if (!hasAnalysis) {
    return `I scanned ${formatNumber(o?.file_count || 0)} files and found ${formatNumber(o?.dir_count || 0)} directories. Running analysis now to identify cleanup opportunities.`;
  }
  const parts: string[] = [];
  if ((o?.duplicate_waste || 0) > 0) parts.push(`duplicate files wasting ${formatBytes(o.duplicate_waste)}`);
  if ((o?.stale_files_size || 0) > 0) parts.push(`${formatBytes(o.stale_files_size)} of unused content`);
  if (parts.length === 0) return `I scanned ${formatNumber(o?.file_count || 0)} files. Everything looks well-organized.`;
  return `I found ${parts.join(", and ")}. Review my suggestions below to safely reclaim space.`;
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-24 animate-pulse rounded-lg bg-gray-200 dark:bg-gray-800" />
      <div className="h-40 animate-pulse rounded-xl bg-gray-200 dark:bg-gray-800" />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="h-48 animate-pulse rounded-lg bg-gray-200 dark:bg-gray-800" />
        <div className="h-48 animate-pulse rounded-lg bg-gray-200 dark:bg-gray-800" />
      </div>
    </div>
  );
}
