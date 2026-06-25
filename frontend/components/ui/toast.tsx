"use client";

import { createContext, useContext, useState, useCallback, ReactNode } from "react";
import { cn } from "@/lib/cn";
import { CheckCircle2, X, AlertCircle, Info } from "lucide-react";

interface Toast {
  id: string;
  type: "success" | "error" | "info";
  title: string;
  message?: string;
  action?: { label: string; onClick: () => void };
}

interface ToastContextValue {
  showToast: (toast: Omit<Toast, "id">) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const showToast = useCallback((toast: Omit<Toast, "id">) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { ...toast, id }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {/* Toast container */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={cn(
              "flex w-80 items-start gap-3 rounded-lg border p-4 shadow-lg",
              "animate-in slide-in-from-bottom-4 fade-in duration-300",
              toast.type === "success" && "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950",
              toast.type === "error" && "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950",
              toast.type === "info" && "border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950",
            )}
          >
            {toast.type === "success" && <CheckCircle2 className="mt-0.5 h-5 w-5 text-green-600" />}
            {toast.type === "error" && <AlertCircle className="mt-0.5 h-5 w-5 text-red-600" />}
            {toast.type === "info" && <Info className="mt-0.5 h-5 w-5 text-blue-600" />}
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{toast.title}</p>
              {toast.message && (
                <p className="mt-0.5 text-sm text-gray-600 dark:text-gray-400">{toast.message}</p>
              )}
              {toast.action && (
                <button
                  onClick={toast.action.onClick}
                  className="mt-2 text-sm font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400"
                >
                  {toast.action.label} →
                </button>
              )}
            </div>
            <button onClick={() => dismiss(toast.id)} className="text-gray-400 hover:text-gray-600">
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
