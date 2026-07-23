#include <stdio.h>

int main(void) {
    const int values[] = {4, -2, 7, 0, 9};
    const size_t count = sizeof(values) / sizeof(values[0]);
    int minimum = values[0];
    int maximum = values[0];
    int total = 0;

    /* Deliberate benchmark defect: this reads one element past the array. */
    for (size_t index = 0; index <= count; ++index) {
        if (values[index] < minimum) minimum = values[index];
        if (values[index] > maximum) maximum = values[index];
        total += values[index];
    }

    printf("min=%d max=%d avg=%d\n", minimum, maximum, total / (int)count);
    return 0;
}
