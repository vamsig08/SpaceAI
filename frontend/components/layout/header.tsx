"use client";

import { ScanSelector } from "@/components/scan/scan-selector";
import { GlobalProgress } from "@/components/layout/global-progress";

export function Header() {
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-gray-200 bg-white/80 px-6 backdrop-blur dark:border-gray-800 dark:bg-gray-950/80">
      <div className="text-sm text-gray-500 dark:text-gray-400">
        <GlobalProgress />
      </div>
      <ScanSelector />
    </header>
  );
}
