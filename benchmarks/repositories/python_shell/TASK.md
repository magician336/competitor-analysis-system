# bench_009：命令注入安全审查

修复 `shell_runner.py` 的 CWE-78 风险。不得拼接 shell 字符串；正常文件名应通过参数列表调用，恶意输入必须被拒绝。同步填写 `SECURITY_REPORT.md`。
