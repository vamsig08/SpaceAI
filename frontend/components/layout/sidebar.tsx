"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";
import {
  BarChart3, Copy, Clock, FolderGit2, Lightbulb,
  TrendingUp, Trash2, LayoutDashboard,
} from "lucide-react";

const navItems = [
  { href: "/", label: "Home", icon: LayoutDashboard, description: "Overview & insights" },
  { href: "/analytics", label: "Storage Map", icon: BarChart3, description: "Where space goes" },
  { href: "/duplicates", label: "Duplicates", icon: Copy, description: "Identical files" },
  { href: "/stale", label: "Unused Files", icon: Clock, description: "Old & forgotten" },
  { href: "/workspaces", label: "Dev Cleanup", icon: FolderGit2, description: "Build artifacts" },
  { href: "/recommendations", label: "Suggestions", icon: Lightbulb, description: "What to clean" },
  { href: "/forecast", label: "Forecast", icon: TrendingUp, description: "Growth trends" },
  { href: "/cleanup", label: "Cleanup", icon: Trash2, description: "Execute & undo" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-30 w-64 border-r border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-950">
      <div className="flex h-16 items-center gap-2.5 border-b border-gray-200 px-6 dark:border-gray-800">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-purple-600">
          <span className="text-sm font-bold text-white">S</span>
        </div>
        <div>
          <span className="text-lg font-bold text-gray-900 dark:text-white">SpaceAI</span>
          <p className="text-[10px] text-gray-400 -mt-0.5">Storage Intelligence</p>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5 p-3" aria-label="Main navigation">
        {navItems.map(({ href, label, icon: Icon, description }) => {
          const isActive = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors",
                isActive
                  ? "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                  : "text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-900 dark:hover:text-gray-100"
              )}
              aria-current={isActive ? "page" : undefined}
            >
              <Icon className="h-4 w-4 shrink-0" />
              <div className="min-w-0">
                <p className="text-sm font-medium leading-tight">{label}</p>
                {!isActive && <p className="text-[11px] text-gray-400 truncate">{description}</p>}
              </div>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
