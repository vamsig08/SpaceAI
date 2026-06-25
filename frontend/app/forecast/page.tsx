"use client";

import { useQuery } from "@tanstack/react-query";
import { getExhaustion, getGrowthRate, getGrowth } from "@/lib/api-client";
import { formatBytes, trendLabel } from "@/lib/format";
import { Card, CardTitle, CardContent } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Badge } from "@/components/ui/badge";
import { TrendingUp, Calendar, Activity } from "lucide-react";

export default function ForecastPage() {
  const { data: exhaustion } = useQuery({ queryKey: ["exhaustion"], queryFn: getExhaustion });
  const { data: growth } = useQuery({ queryKey: ["growth-rate"], queryFn: getGrowthRate });
  const { data: history } = useQuery({ queryKey: ["growth-history"], queryFn: () => getGrowth(90) });

  const e = exhaustion?.data;
  const g = growth?.data;
  const h = history?.data;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold">Storage Forecast</h1>
        <p className="mt-1 text-gray-500 dark:text-gray-400">
          Predict future storage needs based on historical trends
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          title="Days Until Full"
          value={e?.days_until_full ? (e.days_until_full > 9999 ? "999+" : String(e.days_until_full)) : "—"}
          subtitle={e?.exhaustion_date ? `Estimated: ${e.exhaustion_date}` : undefined}
          icon={<Calendar className="h-5 w-5" />}
        />
        <StatCard
          title="Weekly Growth"
          value={g ? formatBytes(g.weekly_growth_bytes) : "—"}
          subtitle={g ? `${formatBytes(g.daily_growth_bytes)}/day` : undefined}
          icon={<TrendingUp className="h-5 w-5" />}
        />
        <StatCard
          title="Trend"
          value={g ? trendLabel(g.trend) : "—"}
          subtitle={g ? `Confidence: ${Math.round(g.confidence * 100)}%` : undefined}
          icon={<Activity className="h-5 w-5" />}
        />
      </div>

      <Card>
        <CardTitle>Growth History ({h?.data_point_count || 0} data points)</CardTitle>
        <CardContent className="mt-4">
          {h && h.data_points.length > 0 ? (
            <div className="space-y-1">
              {h.data_points.slice(-10).map((dp) => (
                <div key={dp.date} className="flex items-center justify-between text-sm">
                  <span className="text-gray-500">{dp.date}</span>
                  <span className="font-medium">{formatBytes(dp.total_size_bytes)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-gray-500">Run multiple scans over time to build growth history for forecasting.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
