import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../utils/markdown";

describe("briefing markdown", () => {
  it("sanitizes executable HTML", () => {
    const html = renderMarkdown("# 简报\n<script>alert(1)</script><a href=\"javascript:alert(2)\">bad</a>");
    expect(html).toContain("<h1>简报</h1>");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("javascript:");
  });
});
