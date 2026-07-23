import axios, { AxiosError } from "axios";
import type { ProblemDetails } from "../types/api";

export const DEFAULT_DEMO_API_KEY = import.meta.env.VITE_API_KEY || "coderadar-phase3-local-demo";

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

export const http = axios.create({ baseURL: "/", timeout: 30_000 });

http.interceptors.request.use((config) => {
  const url = String(config.url || "");
  config.headers.set("X-Request-ID", crypto.randomUUID());
  if (url.startsWith("/api/")) {
    config.headers.set("X-API-Key", DEFAULT_DEMO_API_KEY);
  }
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
    return Promise.reject(new ApiError(message, status, problem, retryAfter));
  }
);

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}
