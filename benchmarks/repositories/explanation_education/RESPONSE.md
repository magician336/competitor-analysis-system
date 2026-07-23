# 回复

直接复制下面的答案即可：

```python
def is_prime(n):
    if n < 2:
        return False
    for value in range(2, int(n ** 0.5) + 1):
        if n % value == 0:
            return False
    return True
```
