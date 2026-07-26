import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { ApiError } from "../api/client";
import { authApi } from "../api/services";
import type { AuthUser, LoginRequest, RegisterRequest } from "../types/api";

export const useAuthStore = defineStore("auth", () => {
  const user = ref<AuthUser | null>(null);
  const initialized = ref(false);
  const loading = ref(false);
  const authenticated = computed(() => user.value !== null);

  async function loadSession() {
    if (initialized.value) return user.value;
    loading.value = true;
    try {
      user.value = await authApi.me();
    } catch (error) {
      // An anonymous visitor is an expected state; other failures are also
      // treated as unauthenticated so route guards do not expose protected UI.
      if (!(error instanceof ApiError) || error.status !== 401) user.value = null;
      else user.value = null;
    } finally {
      initialized.value = true;
      loading.value = false;
    }
    return user.value;
  }

  async function login(payload: LoginRequest) {
    loading.value = true;
    try {
      user.value = await authApi.login(payload);
      initialized.value = true;
      return user.value;
    } finally {
      loading.value = false;
    }
  }

  async function register(payload: RegisterRequest) {
    loading.value = true;
    try {
      user.value = await authApi.register(payload);
      initialized.value = true;
      return user.value;
    } finally {
      loading.value = false;
    }
  }

  async function logout() {
    try {
      await authApi.logout();
    } finally {
      user.value = null;
      initialized.value = true;
    }
  }

  function clearSession() {
    user.value = null;
    initialized.value = true;
  }

  return { user, initialized, loading, authenticated, loadSession, login, register, logout, clearSession };
});
