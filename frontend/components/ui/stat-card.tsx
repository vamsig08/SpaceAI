import { cn } from "@/lib/cn";
import { Card } from "./card";

interface StatCardProps {
  title: string;
  value: string;
  subtitle?: string;
  icon?: React.ReactNode;
  trend?: "up" | "down" | "stable";
  className?: string;
}

export function StatCard({ title, value, subtitle, icon, trend, className }: StatCardProps) {
  return (
    <Card className={cn("flex items-start gap-4", className)}>
      {icon && (
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-400">
          {icon}
        </div>
      )}
      <div className="flex-1">
        <p className="text-sm font-medium text-gray-500 dark:text-gray-400">{title}</p>
        <p className="mt-1 text-2xl font-bold text-gray-900 dark:text-gray-100">{value}</p>
        {subtitle && (
          <p className={cn(
            "mt-1 text-sm",
            trend === "up" ? "text-red-500" : trend === "down" ? "text-green-500" : "text-gray-500"
          )}>
            {subtitle}
          </p>
        )}
      </div>
    </Card>
  );
}
