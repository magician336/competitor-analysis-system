<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { errorMessage } from "../api/client";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const form = reactive({ username: "", password: "", confirmPassword: "" });
const error = ref("");
const usernameValid = computed(() => /^[A-Za-z0-9_-]{3,32}$/.test(form.username));
const passwordValid = computed(() => form.password.length >= 8 && form.password.length <= 72 && /[A-Za-z]/.test(form.password) && /\d/.test(form.password));
const passwordsMatch = computed(() => form.password === form.confirmPassword);
const canSubmit = computed(() => usernameValid.value && passwordValid.value && passwordsMatch.value && !auth.loading);

function destination() {
  const value = typeof route.query.returnTo === "string" ? route.query.returnTo : "/";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

async function submit() {
  if (!canSubmit.value) return;
  error.value = "";
  try {
    await auth.register({ username: form.username.trim(), password: form.password });
    await router.replace(destination());
  } catch (reason) {
    error.value = errorMessage(reason);
  }
}
</script>

<template>
  <main class="auth-page grid-surface">
    <section class="auth-card" aria-labelledby="register-title">
      <router-link to="/" class="auth-brand"><strong>CodeRadar</strong><span>MODEL INTELLIGENCE OBSERVATORY</span></router-link>
      <p class="eyebrow">NEW ACCOUNT / 02</p>
      <h1 id="register-title">创建你的观察席</h1>
      <p class="auth-card__lead">你的问答、检索与报告将只显示在这个账号下。</p>
      <form class="auth-form" @submit.prevent="submit">
        <label>用户名
          <input v-model.trim="form.username" autocomplete="username" maxlength="32" />
        </label>
        <label>密码
          <input v-model="form.password" type="password" autocomplete="new-password" maxlength="72" placeholder="至少 8 位，包含字母和数字" />
        </label>
        <label>确认密码
          <input v-model="form.confirmPassword" type="password" autocomplete="new-password" maxlength="72" placeholder="再次输入密码" />
        </label>
        <p v-if="form.confirmPassword && !passwordsMatch" class="auth-form__error">两次输入的密码不一致</p>
        <p v-else-if="error" class="auth-form__error" role="alert">{{ error }}</p>
        <button class="auth-submit" type="submit" :disabled="!canSubmit">{{ auth.loading ? "创建中…" : "创建账号并进入系统 ↗" }}</button>
      </form>
      <p class="auth-switch">已有账号？<router-link :to="{ path: '/login', query: route.query }">直接登录</router-link></p>
    </section>
  </main>
</template>
