import { computed, ref } from "vue";
import { defineStore } from "pinia";
import type { ProjectDTO } from "@/api/types";
import { api } from "@/api/client";

const ACTIVE_PROJECT_KEY = "my-agent.activeProjectId";

/** Проекты: список, активный проект, раскрытые аккордеоны. */
export const useProjectsStore = defineStore("projects", () => {
  const projects = ref<ProjectDTO[]>([]);
  const activeProjectId = ref<string | null>(null);
  const expanded = ref<Record<string, boolean>>({});
  const loaded = ref(false);
  const loadError = ref<string | null>(null);

  const activeProject = computed<ProjectDTO | null>(() => {
    if (activeProjectId.value === null) return null;
    return projects.value.find((p) => p.id === activeProjectId.value) ?? null;
  });

  function setActive(id: string): void {
    activeProjectId.value = id;
    localStorage.setItem(ACTIVE_PROJECT_KEY, id);
  }

  function toggle(id: string): void {
    expanded.value[id] = !expanded.value[id];
  }

  function isExpanded(id: string): boolean {
    return expanded.value[id] === true;
  }

  function expand(id: string): void {
    expanded.value[id] = true;
  }

  function syncStorage(): void {
    if (activeProjectId.value !== null) localStorage.setItem(ACTIVE_PROJECT_KEY, activeProjectId.value);
    else localStorage.removeItem(ACTIVE_PROJECT_KEY);
  }

  function restoreActive(): void {
    if (projects.value.length === 0) {
      activeProjectId.value = null;
      syncStorage();
      return;
    }
    const saved = localStorage.getItem(ACTIVE_PROJECT_KEY);
    if (saved !== null && projects.value.some((p) => p.id === saved)) {
      activeProjectId.value = saved;
    } else {
      activeProjectId.value = projects.value[0].id;
    }
    expand(activeProjectId.value ?? "");
    syncStorage();
  }

  async function load(): Promise<void> {
    try {
      projects.value = await api.listProjects();
      loadError.value = null;
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    } finally {
      loaded.value = true;
      restoreActive();
    }
  }

  async function reload(): Promise<void> {
    await load();
  }

  async function create(name: string): Promise<ProjectDTO> {
    const project = await api.createProject(name);
    projects.value.push(project);
    setActive(project.id);
    expand(project.id);
    return project;
  }

  async function rename(id: string, name: string): Promise<void> {
    const dto = await api.renameProject(id, name);
    const p = projects.value.find((x) => x.id === id);
    if (p !== undefined) p.name = dto.name;
  }

  async function remove(id: string): Promise<void> {
    await api.deleteProject(id);
    projects.value = projects.value.filter((p) => p.id !== id);
    delete expanded.value[id];
    if (activeProjectId.value === id) {
      activeProjectId.value = projects.value[0]?.id ?? null;
      if (activeProjectId.value !== null) expand(activeProjectId.value);
      syncStorage();
    }
  }

  return {
    projects,
    activeProjectId,
    activeProject,
    expanded,
    loaded,
    loadError,
    load,
    reload,
    create,
    rename,
    remove,
    setActive,
    toggle,
    isExpanded,
    expand,
  };
});
