#include "lru_cache.hpp"

#include <iostream>

int main() {
    int executed = 0;
    int failed = 0;
    const auto expect = [&](bool condition, const char* label) {
        ++executed;
        if (!condition) {
            ++failed;
            std::cerr << "FAILED: " << label << '\n';
        }
    };

    LRUCache cache(2);
    cache.put(1, 10);
    cache.put(2, 20);
    expect(cache.get(1) == 10, "recent value is returned");
    cache.put(3, 30);
    expect(cache.get(2) == -1, "least recently used value is evicted");
    expect(cache.get(3) == 30, "new value remains available");
    cache.put(1, 11);
    expect(cache.get(1) == 11, "existing value can be updated");

    LRUCache zero(0);
    zero.put(9, 90);
    expect(zero.get(9) == -1, "zero-capacity cache stores nothing");

    if (failed != 0 || executed != 5) {
        return 1;
    }
    std::cout << "CODERADAR_BENCH_010_CHECKS_COMPLETE tests=5 failed=0\n";
    return 0;
}
