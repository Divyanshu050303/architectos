/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/projects.py.
 *   GET   /projects
 *   POST  /projects          { name, description }
 *   GET   /projects/{id}
 *   PATCH /projects/{id}     { name?, description? }
 */
import { ProjectListSchema, ProjectSchema } from "@/schemas/projects";
import type { Project, ProjectInput } from "@/types/project";

import { apiPath, request } from "./client";

export function listProjects(signal?: AbortSignal): Promise<Project[]> {
  return request(ProjectListSchema, { method: "GET", path: "/projects", signal });
}

export function createProject(input: ProjectInput): Promise<Project> {
  return request(ProjectSchema, { method: "POST", path: "/projects", body: input });
}

export function getProject(projectId: string, signal?: AbortSignal): Promise<Project> {
  return request(ProjectSchema, { method: "GET", path: apiPath`/projects/${projectId}`, signal });
}

export function updateProject(projectId: string, input: Partial<ProjectInput>): Promise<Project> {
  return request(ProjectSchema, { method: "PATCH", path: apiPath`/projects/${projectId}`, body: input });
}
