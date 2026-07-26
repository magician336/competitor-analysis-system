<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { errorMessage } from "../api/client";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const form = reactive({ username: "", password: "" });
const error = ref("");
const usernameValid = computed(() => /^[A-Za-z0-9_-]{3,32}$/.test(form.username));
const passwordValid = computed(() => form.password.length >= 8 && form.password.length <= 72 && /[A-Za-z]/.test(form.password) && /\d/.test(form.password));
const canSubmit = computed(() => usernameValid.value && passwordValid.value && !auth.loading);

function destination() {
  const value = typeof route.query.returnTo === "string" ? route.query.returnTo : "/";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

async function submit() {
  if (!canSubmit.value) return;
  error.value = "";
  try {
    await auth.login({ username: form.username.trim(), password: form.password });
    await router.replace(destination());
  } catch (reason) {
    error.value = errorMessage(reason);
  }
}
</script>

<template>
  <main class="auth-page grid-surface">
    <section class="auth-card" aria-labelledby="login-title">
      <router-link to="/" class="auth-brand"><strong>CodeRadar</strong><span>MODEL INTELLIGENCE OBSERVATORY</span></router-link>
      <p class="eyebrow">SESSION ACCESS / 01</p>
      <h1 id="login-title">欢迎回来</h1>
      <p class="auth-card__lead">登录后继续查看属于你的随问、证据检索与深度报告记录。</p>
      <form class="auth-form" @submit.prevent="submit">
        <label>用户名
          <input v-model.trim="form.username" autocomplete="username" maxlength="32" />
        </label>
        <label>密码
          <input v-model="form.password" type="password" autocomplete="current-password" maxlength="72" placeholder="输入密码" />
        </label>
        <p v-if="error" class="auth-form__error" role="alert">{{ error }}</p>
        <button class="auth-submit" type="submit" :disabled="!canSubmit">{{ auth.loading ? "登录中…" : "登录并进入系统 ↗" }}</button>
      </form>
      <p class="auth-switch">还没有账号？<router-link :to="{ path: '/register', query: route.query }">创建账号</router-link></p>
    </section>
  </main>
</template>
