import type { Metadata } from "next";
import Link from "next/link";

import { EmptyState } from "@/components/feedback/EmptyState";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-lg flex-col justify-center px-4">
      <EmptyState
        headingLevel={1}
        title="This page does not exist."
        description="The link may be outdated. Your systems are listed on the dashboard."
        action={
          <Button asChild variant="primary">
            <Link href="/dashboard">Go to dashboard</Link>
          </Button>
        }
      />
    </main>
  );
}
