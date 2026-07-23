import MockAdapter from "axios-mock-adapter";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ApiError, DEFAULT_DEMO_API_KEY, http } from "../api/client";

describe("API client", () => {
  let mock: MockAdapter;
  beforeEach(() => { mock = new MockAdapter(http); });
  afterEach(() => mock.restore());

  it("adds API key and request ID to protected requests", async () => {
    mock.onGet("/api/cards").reply((config) => {
      const headers = config.headers as Record<string, unknown>;
      expect(headers["X-API-Key"]).toBe(DEFAULT_DEMO_API_KEY);
      expect(headers["X-Request-ID"]).toBeTruthy();
      return [200, { items: [] }];
    });
    await http.get("/api/cards");
  });

  it("returns a readable API error on 401", async () => {
    mock.onGet("/api/cards").reply(401, { detail: "invalid", code: "invalid_api_key" });
    await expect(http.get("/api/cards")).rejects.toMatchObject({ status: 401, message: "invalid" });
  });

  it("uses Retry-After in 429 feedback", async () => {
    mock.onGet("/api/cards").reply(429, { detail: "limited" }, { "Retry-After": "12" });
    await expect(http.get("/api/cards")).rejects.toThrow("12 秒后重试");
  });
});
