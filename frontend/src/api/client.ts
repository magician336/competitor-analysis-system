import axios, { AxiosError } from "axios";
import type { ProblemDetails } from "../types/api";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly problem?: ProblemDetails,
    public readonly retryAfter?: number
  ) {
    super(message);
  }
}

// Browser requests authenticate through the HttpOnly session cookie.  API-key
// authentication remains available to non-browser clients on the backend.
export const http = axios.create({ baseURL: "/", timeout: 30_000, withCredentials: true });

http.interceptors.request.use((config) => {
  config.headers.set("X-Request-ID", crypto.randomUUID());
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ProblemDetails>) => {
    const status = error.response?.status ?? 0;
    const problem = error.response?.data;
    const responseHeaders = error.response?.headers as Record<string, unknown> | undefined;
    const retryHeader = responseHeaders?.["retry-after"] ?? responseHeaders?.["Retry-After"];
    const retryAfter = retryHeader ? Number(retryHeader) : undefined;
    const message = status === 429 && retryAfter
      ? `请求过于频繁，请在 ${retryAfter} 秒后重试`
      : problem?.detail || error.message || "请求失败";
    const url = String(error.config?.url || "");
    if (status === 401 && url.startsWith("/api/") && !url.startsWith("/api/auth/")) {
      window.dispatchEvent(new CustomEvent("coderadar:unauthorized"));
    }
    return Promise.reject(new ApiError(message, status, problem, retryAfter));
  }
);

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}
