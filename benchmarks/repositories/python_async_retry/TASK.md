# bench_013：异步重试器修复

根据失败验收修复 `retrying.py` 中的 `retry()`。

## 接口约定

- `max_attempts` 表示包含首次调用在内的最大尝试次数；
- 成功时立即返回，耗尽次数时重新抛出最后一次业务异常；
- `asyncio.CancelledError` 必须立即传播且不得触发重试；
- 退避序列为 `base_delay * 2**n`；
- 必须使用传入的异步 `sleep`，以便验收使用虚拟时钟且不真实等待。

不得修改 `checks.py` 或 `validator.json`，不得加入固定等待。
