import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createProject, getProject, listProjects, updateProject } from "@/api/projects";
import { track } from "@/lib/analytics";
import { queryKeys } from "@/lib/query-keys";
import type { Project, ProjectInput } from "@/types/project";

export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects(),
    queryFn: ({ signal }) => listProjects(signal),
  });
}

export function useProject(projectId: string) {
  return useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: ({ signal }) => getProject(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ProjectInput) => createProject(input),
    onSuccess: (project) => {
      queryClient.setQueryData<Project>(queryKeys.project(project.id), project);
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects() });
      track("project_created", { projectId: project.id });
    },
  });
}

export function useUpdateProject(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<ProjectInput>) => updateProject(projectId, input),
    onSuccess: (project) => {
      queryClient.setQueryData<Project>(queryKeys.project(projectId), project);
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects() });
    },
  });
}
