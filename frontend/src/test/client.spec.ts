import MockAdapter from "axios-mock-adapter";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ApiError, http } from "../api/client";

describe("API client", () => {
  let mock: MockAdapter;
  beforeEach(() => { mock = new MockAdapter(http); });
  afterEach(() => mock.restore());

  it("uses credentials and adds a request ID without exposing an API key", async () => {
    mock.onGet("/api/cards").reply((config) => {
      const headers = config.headers as Record<string, unknown>;
      expect(headers["X-API-Key"]).toBeUndefined();
      expect(headers["X-Request-ID"]).toBeTruthy();
      expect(config.withCredentials).toBe(true);
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
