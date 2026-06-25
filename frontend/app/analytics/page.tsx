"use client";

import { useQuery } from "@tanstack/react-query";
import { getCategories, getLargestFiles, getLargestFolders } from "@/lib/api-client";
import { formatBytes } from "@/lib/format";
import { Card, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function AnalyticsPage() {
  const { data: categories } = useQuery({ queryKey: ["categories"], queryFn: () => getCategories() });
  const { data: largestFiles } = useQuery({ queryKey: ["largest-files"], queryFn: () => getLargestFiles(undefined, 20) });
  const { data: largestFolders } = useQuery({ queryKey: ["largest-folders"], queryFn: () => getLargestFolders(undefined, 20) });

  const breakdown = categories?.data?.breakdown || {};
  const totalBytes = categories?.data?.total_bytes || 0;

  const categoryColors: Record<string, string> = {
    video: "bg-purple-500", image: "bg-pink-500", document: "bg-blue-500",
    code: "bg-green-500", audio: "bg-yellow-500", archive: "bg-orange-500",
    data: "bg-red-500", other: "bg-gray-400",
  };

  return (
    <div className="space-y-8">
      <h1 className="text-3xl font-bold">Storage Analytics</h1>

      {/* Category Breakdown */}
      <Card>
        <CardTitle>File Categories</CardTitle>
        <CardContent className="mt-4">
          {totalBytes > 0 ? (
            <>
              <div className="mb-4 flex h-4 overflow-hidden rounded-full">
                {Object.entries(breakdown)
                  .sort(([, a], [, b]) => b - a)
                  .map(([cat, bytes]) => (
                    <div
                      key={cat}
                      className={`${categoryColors[cat] || "bg-gray-400"}`}
                      style={{ width: `${(bytes / totalBytes) * 100}%` }}
                      title={`${cat}: ${formatBytes(bytes)}`}
                    />
                  ))}
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {Object.entries(breakdown)
                  .sort(([, a], [, b]) => b - a)
                  .map(([cat, bytes]) => (
                    <div key={cat} className="flex items-center gap-2">
                      <div className={`h-3 w-3 rounded-full ${categoryColors[cat] || "bg-gray-400"}`} />
                      <span className="text-sm capitalize">{cat}</span>
                      <span className="ml-auto text-sm text-gray-500">{formatBytes(bytes)}</span>
                    </div>
                  ))}
              </div>
            </>
          ) : (
            <p className="text-gray-500">No scan data available. Run a scan first.</p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Largest Files */}
        <Card>
          <CardTitle>Largest Files</CardTitle>
          <CardContent className="mt-4">
            <div className="space-y-2">
              {(largestFiles?.data?.files || []).slice(0, 10).map((file) => (
                <div key={file.id} className="flex items-center justify-between rounded-md border border-gray-100 p-2 dark:border-gray-800">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{file.filename}</p>
                    <p className="truncate text-xs text-gray-500">{file.path}</p>
                  </div>
                  <div className="ml-2 flex items-center gap-2">
                    {file.category && <Badge>{file.category}</Badge>}
                    <span className="whitespace-nowrap text-sm font-medium">{formatBytes(file.size_bytes)}</span>
                  </div>
                </div>
              ))}
              {!largestFiles?.data?.files?.length && (
                <p className="text-gray-500">No data available</p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Largest Folders */}
        <Card>
          <CardTitle>Largest Folders</CardTitle>
          <CardContent className="mt-4">
            <div className="space-y-2">
              {(largestFolders?.data?.folders || []).slice(0, 10).map((folder) => (
                <div key={folder.id} className="flex items-center justify-between rounded-md border border-gray-100 p-2 dark:border-gray-800">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{folder.name}</p>
                    <p className="truncate text-xs text-gray-500">{folder.path}</p>
                  </div>
                  <span className="ml-2 whitespace-nowrap text-sm font-medium">{formatBytes(folder.total_size_bytes)}</span>
                </div>
              ))}
              {!largestFolders?.data?.folders?.length && (
                <p className="text-gray-500">No data available</p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
