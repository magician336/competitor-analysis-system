# bench_004：Java 泛型栈修复

修复 `Stack<T>`，保证泛型类型安全；`peek`/`pop` 在空栈时抛出
`IllegalStateException`。验证使用本机 `javac` 的 `-Xlint:all -Werror`，不依赖 Maven。
