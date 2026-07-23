# bench_016：下载接口路径安全

修复 `download.py`，阻止目录穿越、绝对路径、符号链接逃逸和错误信息泄露，同时保持合法文件读取。填写 `SECURITY_REPORT.md`，不得修改 `checks.py`。
