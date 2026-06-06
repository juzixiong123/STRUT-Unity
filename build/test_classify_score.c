#include "unity.h"

int classify_score(int score);

void setUp(void) {}
void tearDown(void) {}

static void test_classify_score_1(void)
{
    TEST_ASSERT_EQUAL_INT(-1, classify_score(-1));
}

static void test_classify_score_2(void)
{
    TEST_ASSERT_EQUAL_INT(0, classify_score(0));
}

static void test_classify_score_3(void)
{
    TEST_ASSERT_EQUAL_INT(0, classify_score(1));
}

static void test_classify_score_4(void)
{
    TEST_ASSERT_EQUAL_INT(0, classify_score(59));
}

static void test_classify_score_5(void)
{
    TEST_ASSERT_EQUAL_INT(1, classify_score(60));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_classify_score_1);
    RUN_TEST(test_classify_score_2);
    RUN_TEST(test_classify_score_3);
    RUN_TEST(test_classify_score_4);
    RUN_TEST(test_classify_score_5);
    return UNITY_END();
}
