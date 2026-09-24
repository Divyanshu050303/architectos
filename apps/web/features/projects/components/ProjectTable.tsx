import Link from "next/link";

import { StatusBadge } from "@/components/ui/badge";
import { Table, Td, Th } from "@/components/ui/table";
import { projectHref } from "@/config/navigation";
import { formatCompact, formatPercent, formatRelativeTime } from "@/lib/formatting";
import type { Project } from "@/types/project";

/** Dense list of every project. */
export function ProjectTable({ projects }: { projects: readonly Project[] }) {
  return (
    <Table>
      <caption className="sr-only">Projects</caption>
      <thead>
        <tr>
          <Th>Name</Th>
          <Th>Status</Th>
          <Th className="text-right">Version</Th>
          <Th className="text-right">DAU</Th>
          <Th className="text-right">Peak RPS</Th>
          <Th className="text-right">Capacity</Th>
          <Th className="hidden md:table-cell">Top issue</Th>
          <Th className="text-right">Updated</Th>
        </tr>
      </thead>
      <tbody>
        {projects.map((project) => (
          <tr key={project.id} className="hover:bg-surface-2">
            <Td className="max-w-64">
              <Link
                href={projectHref(project.id)}
                className="block truncate font-medium text-fg hover:underline"
              >
                {project.name}
              </Link>
              {project.description ? (
                <span className="block truncate text-xs text-muted">{project.description}</span>
              ) : null}
            </Td>
            <Td>
              <StatusBadge status={project.summary.status} />
            </Td>
            <Td className="tabular text-right">
              {project.architectureVersion === null ? "—" : `v${project.architectureVersion}`}
            </Td>
            <Td className="tabular text-right">{formatCompact(project.summary.dailyActiveUsers)}</Td>
            <Td className="tabular text-right">{formatCompact(project.summary.peakRps)}</Td>
            <Td className="tabular text-right">{formatPercent(project.summary.capacityUtilization)}</Td>
            <Td className="hidden max-w-72 truncate text-xs text-fg-secondary md:table-cell">
              {project.summary.topIssue ?? "—"}
            </Td>
            <Td className="tabular text-right text-xs whitespace-nowrap text-muted">
              <time dateTime={project.updatedAt}>{formatRelativeTime(project.updatedAt)}</time>
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
