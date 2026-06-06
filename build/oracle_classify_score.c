#include <stdio.h>
int classify_score(int score);
int main(void)
{
    printf("%d\n", classify_score(-1));
    printf("%d\n", classify_score(0));
    printf("%d\n", classify_score(1));
    printf("%d\n", classify_score(59));
    printf("%d\n", classify_score(60));
    return 0;
}
