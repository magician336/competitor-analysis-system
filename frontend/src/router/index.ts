import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: () => import("../views/HomeView.vue") },
    { path: "/ask", name: "ask", component: () => import("../views/AskView.vue") },
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

export default router;
