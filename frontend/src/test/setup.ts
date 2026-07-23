import { vi } from "vitest";

class ResizeObserverMock {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element) {
    const rect = target.getBoundingClientRect();
    this.callback([{
      target,
      contentRect: rect,
      borderBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
      contentBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
      devicePixelContentBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
    } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal("ResizeObserver", ResizeObserverMock);
if (!globalThis.crypto?.randomUUID) {
  vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000000" });
}
