import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf-8");
const analysisView = readFileSync(resolve(process.cwd(), "src/views/AnalysisView.vue"), "utf-8");

describe("analysis visual refresh", () => {
  it("uses mode-derived muted accents and a 40/60 report split", () => {
    expect(styles).toMatch(/--analysis-accent:var\(--mode-start\)/);
    expect(styles).not.toContain("--analysis-accent:#6f7f86");
    expect(styles).toMatch(/\.report-split \{[^}]*minmax\(400px,40%\)[^}]*minmax\(0,60%\)/);
    expect(styles).toMatch(/\.report-sidebar \{[^}]*background-color: var\(--paper\)/);
    expect(styles).toMatch(/\.artifact-download \{[^}]*var\(--mode-start\)/);
  });

  it("keeps the report history action while removing progress and action headers", () => {
    expect(analysisView).not.toContain('label="进度"');
    expect(analysisView).not.toContain('label="操作"');
    expect(analysisView).toContain('prop="competitor" label="分析对象" min-width="150"');
    expect(analysisView).toContain('label="分析方式" min-width="130"');
    expect(analysisView).toContain('prop="submitted_at" label="提交时间" width="260"');
    expect(analysisView).toContain('label="" width="120"');
    expect(analysisView).toContain("打开报告");
  });

  it("shares the primary title treatment and redesigned advanced controls", () => {
    expect(analysisView).toContain("primary-page-intro");
    expect(analysisView).toContain("advanced-number-card");
    expect(analysisView).toContain("advanced-output-grid");
    expect(analysisView).toContain("includeSnapshot: true");
    expect(analysisView).toContain("includeBriefing: true");
    expect(styles).toMatch(/\.primary-page-intro h1 \{[^}]*clamp\(34px,4vw,54px\)/);
    expect(styles).toMatch(/\.artifact-title > b \{[^}]*font-size: 14px/);
    expect(styles).toMatch(/\.wizard-actions \.el-button--primary \{[^}]*var\(--analysis-accent\)[^}]*var\(--mode-mid\)/);
  });
});
