// Профили роли: глобальный пул + привязка к проекту.
// Глобальные профили — общий реестр; проекты выбирают себе подмножество.
import { ref } from "vue";
import { defineStore } from "pinia";
import type { ProfileDTO } from "@/api/types";
import { api } from "@/api/client";
import { useProjectsStore } from "./projects";

export const useProfilesStore = defineStore("profiles", () => {
  const profiles = ref<ProfileDTO[]>([]);
  /** Кэш: project_id -> профили, привязанные к проекту. */
  const projectProfiles = ref<Record<string, ProfileDTO[]>>({});
  const loaded = ref(false);
  const loadError = ref<string | null>(null);

  function profileById(id: string): ProfileDTO | undefined {
    return profiles.value.find((p) => p.id === id);
  }

  /** Профили, доступные для проекта (пустой массив — не загружены). */
  function profilesOfProject(projectId: string): ProfileDTO[] {
    return projectProfiles.value[projectId] ?? [];
  }

  async function load(): Promise<void> {
    try {
      profiles.value = await api.listProfiles();
      loadError.value = null;
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    } finally {
      loaded.value = true;
    }
  }

  /** Подтягивает профили проекта (для селектора в чате / панели управления). */
  async function loadProjectProfiles(projectId: string): Promise<void> {
    if (!projectId) return;
    try {
      projectProfiles.value[projectId] = await api.getProjectProfiles(projectId);
    } catch {
      projectProfiles.value[projectId] = [];
    }
  }

  async function create(name: string, content: string): Promise<ProfileDTO> {
    const profile = await api.createProfile(name, content);
    profiles.value.push(profile);
    return profile;
  }

  async function update(id: string, patch: { name?: string; content?: string }): Promise<void> {
    const updated = await api.updateProfile(id, patch);
    const idx = profiles.value.findIndex((p) => p.id === id);
    if (idx !== -1) profiles.value[idx] = updated;
    // в проект может быть привязан — обновляем и кэш проектов
    for (const key of Object.keys(projectProfiles.value)) {
      const list = projectProfiles.value[key];
      const i = list.findIndex((p) => p.id === id);
      if (i !== -1) list[i] = updated;
    }
  }

  async function remove(id: string): Promise<void> {
    await api.deleteProfile(id);
    profiles.value = profiles.value.filter((p) => p.id !== id);
    // удалённый профиль выпадает из всех проектов и активных чатов
    for (const key of Object.keys(projectProfiles.value)) {
      projectProfiles.value[key] = projectProfiles.value[key].filter((p) => p.id !== id);
    }
  }

  /** Заменяет набор профилей проекта; обновляет кэш и store проектов. */
  async function bind(projectId: string, profileIds: string[]): Promise<void> {
    const list = await api.setProjectProfiles(projectId, profileIds);
    projectProfiles.value[projectId] = list;
    const projectsStore = useProjectsStore();
    const project = projectsStore.projects.find((p) => p.id === projectId);
    if (project !== undefined) project.profile_ids = profileIds;
  }

  return {
    profiles,
    projectProfiles,
    loaded,
    loadError,
    profileById,
    profilesOfProject,
    load,
    loadProjectProfiles,
    create,
    update,
    remove,
    bind,
  };
});
