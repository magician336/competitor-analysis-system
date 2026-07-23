#pragma once

#include <cstddef>

class LRUCache {
public:
    explicit LRUCache(std::size_t capacity) : capacity_(capacity) {}

    int get(int key) {
        (void)key;
        return -1; // deliberate TODO
    }

    void put(int key, int value) {
        (void)key;
        (void)value; // deliberate TODO
    }

private:
    std::size_t capacity_;
};
