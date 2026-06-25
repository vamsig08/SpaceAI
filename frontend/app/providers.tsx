"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ScanProvider } from "@/lib/scan-context";
import { ToastProvider } from "@/components/ui/toast";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000,
            retry: 1,
            // Don't throw on failed queries — show empty states instead
            throwOnError: false,
          },
          mutations: {
            // Don't throw on failed mutations — onError handles it
            throwOnError: false,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ScanProvider>
          {children}
        </ScanProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
