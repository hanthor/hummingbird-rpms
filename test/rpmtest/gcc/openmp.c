#include <stdio.h>

int main(void) {
    long total = 0;

#pragma omp parallel for reduction(+ : total)
    for (int i = 0; i < 1000; ++i) {
        total += i;
    }
    printf("%ld\n", total);
    return total == 499500 ? 0 : 1;
}
