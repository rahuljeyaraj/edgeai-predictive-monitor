/*
 * test_util.h — shared PASS/FAIL/EXPECTED-FAIL reporter for tests/host/ binaries.
 *
 * A regular assert()-based test aborts the process the instant a documented,
 * known bug reproduces, which makes "expected to currently fail" tests
 * indistinguishable from a build break. test_report() instead prints one of
 * four outcomes and only test_summary()'s exit code distinguishes a genuine
 * regression from a documented one:
 *   [PASS]             expected to pass, did
 *   [FAIL]             expected to pass, didn't  -> regression, nonzero exit
 *   [EXPECTED-FAIL]    expected to fail (known bug), still does  -> exit 0
 *   [UNEXPECTED-PASS]  expected to fail, now passes -> bug likely fixed
 *                      upstream; this test's expectation is stale, nonzero
 *                      exit so it gets noticed and updated
 */
#pragma once
#include <stdio.h>

typedef enum { EXPECT_PASS, EXPECT_FAIL } test_expect_t;

static int g_test_total = 0;
static int g_test_unexpected = 0;

static void test_report(const char *name, int ok, test_expect_t expect, const char *detail)
{
    g_test_total++;
    if (expect == EXPECT_PASS) {
        if (ok) {
            printf("[PASS] %s: %s\n", name, detail);
        } else {
            printf("[FAIL] %s: %s\n", name, detail);
            g_test_unexpected++;
        }
    } else {
        if (!ok) {
            printf("[EXPECTED-FAIL] %s: %s (known bug, see Phase 2)\n", name, detail);
        } else {
            printf("[UNEXPECTED-PASS] %s: %s (looks fixed -- update this test's expectation)\n",
                   name, detail);
            g_test_unexpected++;
        }
    }
}

static int test_summary(void)
{
    printf("RESULT: %s (%d checks, %d unexpected)\n",
           g_test_unexpected == 0 ? "PASS" : "FAIL", g_test_total, g_test_unexpected);
    return g_test_unexpected == 0 ? 0 : 1;
}
