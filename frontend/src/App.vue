<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAppStore } from "./stores/app";
import { useAuthStore } from "./stores/auth";

const store = useAppStore();
const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const activeNavigation = computed(() => {
  if (route.path.startsWith("/analysis")) return "/analysis";
  if (route.path.startsWith("/admin")) return "/admin";
  return route.path;
});
const heroProgress = ref(0);
const homeRoute = computed(() => route.path === "/");
const authPage = computed(() => Boolean(route.meta.authPage));
const headerStyle = computed(() => {
  if (!homeRoute.value) return undefined;
  const progress = Math.min(1, Math.max(0, (heroProgress.value - 0.18) / 0.7));
  return {
    "--hero-progress": heroProgress.value,
    "--header-color": "#15171b",
    "--header-bg": `rgba(244, 241, 234, ${0.68 + progress * 0.22})`,
    "--header-border": `rgba(25, 31, 42, ${0.08 + progress * 0.1})`,
  };
});

const navigation = [
  ["/", "趋势观测"],
  ["/ask", "AI 随问"],
  ["/evidence", "证据检索"],
  ["/analysis", "深度报告"],
  ["/admin", "系统管理"]
];

function updateHeroProgress(event: Event) {
  heroProgress.value = Math.min(1, Math.max(0, Number((event as CustomEvent<number>).detail) || 0));
}

function redirectAfterUnauthorized() {
  if (authPage.value) return;
  auth.clearSession();
  void router.replace({ path: "/login", query: { returnTo: route.fullPath } });
}

onMounted(() => {
  if (!authPage.value) {
    void store.refreshStatus();
    void store.loadCompetitors();
  }
  window.addEventListener("coderadar:hero-progress", updateHeroProgress);
  window.addEventListener("coderadar:unauthorized", redirectAfterUnauthorized);
});
onBeforeUnmount(() => {
  window.removeEventListener("coderadar:hero-progress", updateHeroProgress);
  window.removeEventListener("coderadar:unauthorized", redirectAfterUnauthorized);
});

async function logout() {
  await auth.logout();
  await router.replace("/login");
}
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell--home': homeRoute, 'app-shell--auth': authPage }">
    <header
      v-if="!authPage"
      class="site-header"
      :class="{ 'site-header--hero': homeRoute && heroProgress < 0.78 }"
      :style="headerStyle"
    >
      <div class="site-header__inner">
        <router-link to="/" class="brand" aria-label="CodeRadar 首页">
          <strong>CodeRadar</strong>
          <span>Model Intelligence Observatory</span>
        </router-link>
        <nav class="navigation" aria-label="主导航">
          <router-link
            v-for="item in navigation"
            :key="item[0]"
            :to="item[0]"
            :class="{ active: activeNavigation === item[0] }"
          >{{ item[1] }}</router-link>
        </nav>
        <div class="site-header__actions">
          <span class="system-state" :title="store.ready ? `ES ${store.ready.backend.status} · ${store.ready.indexed_chunks} chunks` : '服务状态'">
            <i class="status-dot" :class="{ online: store.apiOnline }" />
            <span>{{ store.apiOnline ? "服务在线" : "服务离线" }}</span>
          </span>
          <div v-if="auth.user" class="account-menu">
            <span class="account-menu__name" :title="auth.user.username">{{ auth.user.username }}</span>
            <button type="button" @click="logout">退出</button>
          </div>
        </div>
      </div>
    </header>
    <main class="main-content" :class="{ 'main-content--home': homeRoute }">
      <router-view v-slot="{ Component, route: viewRoute }">
        <transition name="route-shift" mode="out-in">
          <component :is="Component" :key="viewRoute.fullPath" />
        </transition>
      </router-view>
    </main>
  </div>
</template>
