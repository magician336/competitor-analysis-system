import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import CapabilityMatrix from "../components/charts/CapabilityMatrix.vue";
import AbilityStar from "../components/charts/AbilityStar.vue";
import CapabilityScoreTable from "../components/CapabilityScoreTable.vue";
import TrendChart from "../components/charts/TrendChart.vue";
import router from "../router";
import type { CapabilitySnapshot, MatrixRow, SnapshotSummary } from "../types/api";

describe("frontend scope and chart semantics", () => {
  it("has four clear product entrances, result routes, and no Benchmark route", () => {
    const paths = router.getRoutes().map((route) => route.path.toLowerCase());
    expect(paths).toEqual(expect.arrayContaining(["/", "/ask", "/evidence", "/analysis", "/admin", "/admin/documents", "/analysis/:workflowid", "/analysis/:workflowid/report"]));
    expect(paths.some((path) => path.includes("benchmark"))).toBe(false);
  });

  it("keeps insufficient dimensions out of the ability polygon", () => {
    const snapshot: CapabilitySnapshot = {
      snapshot_id: "s1", competitor: "Cursor", snapshot_date: "2026-07-20", product_version: "unknown", scoring_version: "v1",
      scores: {}, confidence: {}, evidence_count: {}, total_score: 72, overall_confidence: .55, coverage_ratio: .28, deltas: {},
      details: [
        { dimension: "code_intelligence", score: 72, status: "scored", confidence: .55, evidence_count: 2, rationale: "evidence" },
        { dimension: "agent_context", score: 0, status: "insufficient_evidence", confidence: 0, evidence_count: 0, rationale: "N/A" }
      ]
    };
    const wrapper = mount(AbilityStar, { props: { snapshot } });
    expect(wrapper.find('[data-status="scored"]').exists()).toBe(true);
    expect(wrapper.find('[data-status="insufficient_evidence"]').exists()).toBe(true);
  });

  it("shows only scored dimensions in the analysis score table and lists missing dimensions as N/A", () => {
    const snapshot: CapabilitySnapshot = {
      snapshot_id: "s2", competitor: "Cursor", snapshot_date: "2026-07-22", product_version: "mixed", scoring_version: "v1",
      scores: {}, confidence: {}, evidence_count: {}, total_score: 50, overall_confidence: .4, coverage_ratio: .28, deltas: {},
      details: [
        { dimension: "code_intelligence", score: 72, status: "scored", confidence: .45, evidence_count: 3, rationale: "evidence" },
        { dimension: "education_fit", score: 0, status: "insufficient_evidence", confidence: 0, evidence_count: 0, rationale: "N/A" }
      ]
    };
    const wrapper = mount(CapabilityScoreTable, { props: { snapshot } });
    expect(wrapper.findAll('tbody [data-status="scored"]')).toHaveLength(1);
    expect(wrapper.text()).toContain("D1");
    expect(wrapper.text()).toContain("72");
    expect(wrapper.text()).toContain("D7 教育适配 · N/A");
    expect(wrapper.text()).not.toContain("本次总分");
  });

  it("renders insufficient evidence as N/A instead of zero", () => {
    const rows: MatrixRow[] = [{
      dimension: "code_intelligence",
      valid_product_count: 1,
      mean_score: 72,
      score_spread: 0,
      cells: [
        { product: "CodeMate Campus", dimension: "code_intelligence", status: "scored", score: 72, confidence: .55, evidence_count: 0, gap_to_baseline: 0, delta_from_previous: null },
        { product: "Cursor", dimension: "code_intelligence", status: "insufficient_evidence", score: null, confidence: 0, evidence_count: 0, gap_to_baseline: null, delta_from_previous: null }
      ]
    }];
    const wrapper = mount(CapabilityMatrix, { props: { rows, products: ["CodeMate Campus", "Cursor"] } });
    expect(wrapper.text()).toContain("N/A");
    expect(wrapper.text()).toContain("72");
  });

  it("renders trend points for Chinese competitor names", () => {
    const snapshots: SnapshotSummary[] = [
      { snapshot_id: "s1", competitor: "通义灵码", snapshot_date: "2026-07-20", product_version: "unknown", scoring_version: "v1", total_score: 45, overall_confidence: .5, coverage_ratio: .6, source_kind: "agent_evidence", created_at: "2026-07-20T00:00:00Z" },
      { snapshot_id: "s2", competitor: "通义灵码", snapshot_date: "2026-07-21", product_version: "unknown", scoring_version: "v1", total_score: 50, overall_confidence: .6, coverage_ratio: .7, source_kind: "agent_evidence", created_at: "2026-07-21T00:00:00Z" }
    ];
    const wrapper = mount(TrendChart, { props: { snapshots } });
    expect(wrapper.findAll("circle")).toHaveLength(3);
    expect(wrapper.find('[data-product="通义灵码"]').exists()).toBe(true);
  });
});
