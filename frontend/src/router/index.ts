import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: () => import("../views/LoginView.vue"), meta: { public: true, authPage: true } },
    { path: "/register", name: "register", component: () => import("../views/RegisterView.vue"), meta: { public: true, authPage: true } },
    { path: "/", name: "home", component: () => import("../views/HomeView.vue") },
    { path: "/ask", name: "ask", component: () => import("../views/AskView.vue") },
    { path: "/evidence", name: "evidence", component: () => import("../views/EvidenceSearchView.vue") },
    { path: "/admin", name: "admin", component: () => import("../views/AdminView.vue") },
    { path: "/admin/documents", name: "admin-documents", component: () => import("../views/AdminDocumentsView.vue") },
    { path: "/analysis", name: "analysis", component: () => import("../views/AnalysisView.vue") },
    { path: "/analysis/:workflowId", name: "analysis-result", component: () => import("../views/AnalysisResultView.vue") },
    { path: "/analysis/:workflowId/report", name: "analysis-report", component: () => import("../views/AnalysisReportView.vue") },
    { path: "/overview", redirect: "/" },
    { path: "/workflows", redirect: "/analysis" },
    { path: "/briefings", redirect: { path: "/analysis", query: { tab: "history" } } },
    { path: "/cards", redirect: "/" },
    { path: "/capabilities", redirect: "/" },
    { path: "/trends", redirect: "/" },
    { path: "/:pathMatch(.*)*", name: "not-found", component: () => import("../views/NotFoundView.vue") }
  ]
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  await auth.loadSession();
  const isAuthPage = Boolean(to.meta.authPage);
  if (auth.authenticated && isAuthPage) return { path: "/" };
  if (!to.meta.public && !auth.authenticated) {
    return { path: "/login", query: { returnTo: to.fullPath } };
  }
  return true;
});

export default router;
