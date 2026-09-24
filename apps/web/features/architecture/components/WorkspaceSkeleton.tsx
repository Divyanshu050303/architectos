import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";

/** Placeholder for the whole workspace while the architecture loads (spec §80). */
export function WorkspaceSkeleton() {
  return (
    <SkeletonGroup label="Loading architecture" className="flex h-full min-h-[32rem] flex-col">
      <div className="flex h-11 items-center gap-2 border-b border-default bg-surface px-3">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-6 w-24" />
        <Skeleton className="ml-auto h-6 w-40" />
      </div>
      <div className="relative flex-1 overflow-hidden">
        <Skeleton className="absolute top-[18%] left-[20%] h-28 w-56 rounded-md" />
        <Skeleton className="absolute top-[18%] left-[55%] h-28 w-56 rounded-md" />
        <Skeleton className="absolute top-[55%] left-[38%] h-28 w-56 rounded-md" />
      </div>
      <div className="border-t border-default bg-surface px-3 py-2.5">
        <Skeleton className="h-11 w-full rounded-md" />
      </div>
    </SkeletonGroup>
  );
}
