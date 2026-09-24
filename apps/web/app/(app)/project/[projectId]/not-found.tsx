import Link from "next/link";

import { EmptyState } from "@/components/feedback/EmptyState";
import { Button } from "@/components/ui/button";

export default function ProjectNotFound() {
  return (
    <div className="mx-auto w-full max-w-lg px-4 py-10">
      <EmptyState
        headingLevel={1}
        title="This section does not exist."
        description="Use the sidebar to open a section of this project, or return to the dashboard."
        action={
          <Button asChild variant="secondary">
            <Link href="/dashboard">Go to dashboard</Link>
          </Button>
        }
      />
    </div>
  );
}
