# bench_005：FastAPI 图书搜索接口

在现有小型 FastAPI 项目中新增按标题搜索图书的接口，同时保持已有接口兼容。

## 验收契约

- 新接口：`GET /books/search?title=<关键词>`；
- 标题搜索忽略大小写并采用子串匹配；
- 响应项稳定包含 `id`、`title`、`author`；
- `GET /books` 与 `GET /books/{book_id}` 不得回归；
- schema、业务查询和 HTTP 路由继续由各自模块负责。

不得修改 `checks.py` 或 `validator.json`。
