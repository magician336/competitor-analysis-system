# bench_002：C 数组统计修复

修复 `main.c` 中的循环边界和输出，使程序在严格警告下编译，并精确输出：

```text
max=9 min=-2 average=3.60
```

不得写死统计结果。验证命令由根目录统一 Runner 调用本目录 `validate.py`。
