# bench_015：为课程选课服务生成 JUnit 5 测试

只修改 `EnrollmentServiceTest.java`，使用 `org.junit.jupiter.api.Test` 和 Assertions 风格，为 `EnrollmentService` 生成测试。

## 必测行为

- 容量边界：容量内成功，满员后拒绝；
- 同一学生重复选课必须被拒绝；
- 缺少任一先修课必须被拒绝；
- 多线程同时为同一学生提交时，最多一次成功且最终只保留一条记录；
- 测试互相独立，没有执行顺序依赖。

仓库提供一个只覆盖本任务所需 API 的离线 JUnit 5 兼容层，因此验证只依赖 JDK 17+，无需下载 Maven 依赖。测试仍使用标准的 `org.junit.jupiter.api` 导入，迁移到正式 JUnit 5 时无需改写核心断言。

验证器会在参考实现上重复运行三次，再运行 5 个冻结缺陷变体；至少杀死 75% 的变体。不得修改生产源码、`validator_support/`、`validator_assets/`、验证器或元数据。

