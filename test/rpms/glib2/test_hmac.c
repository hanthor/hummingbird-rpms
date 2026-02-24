#include <glib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

static void
check_str(const char *test_name, const char *expected, const char *actual)
{
    if (actual == NULL) {
        fprintf(stderr, "FAIL [%s]: got NULL\n", test_name);
        failures++;
    } else if (strcmp(expected, actual) != 0) {
        fprintf(stderr, "FAIL [%s]:\n  expected: %s\n  got:      %s\n",
                test_name, expected, actual);
        failures++;
    } else {
        printf("PASS: %s\n", test_name);
    }
}

static void
check_digest(const char *test_name, const guint8 *expected,
             const guint8 *actual, gsize len)
{
    if (memcmp(expected, actual, len) != 0) {
        fprintf(stderr, "FAIL [%s]: binary digest mismatch\n", test_name);
        fprintf(stderr, "  expected:");
        for (gsize i = 0; i < len; i++)
            fprintf(stderr, " %02x", expected[i]);
        fprintf(stderr, "\n  got:     ");
        for (gsize i = 0; i < len; i++)
            fprintf(stderr, " %02x", actual[i]);
        fprintf(stderr, "\n");
        failures++;
    } else {
        printf("PASS: %s\n", test_name);
    }
}

/* RFC 4231 Test Case 2 - used across multiple tests */
static const char *tc2_key = "Jefe";
static const char *tc2_data = "what do ya want for nothing?";

static const char *tc2_sha256 =
    "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843";
static const char *tc2_sha384 =
    "af45d2e376484031617f78d2b58a6b1b9c7ef464f5a01b47e42ec3736322445e"
    "8e2240ca5e69e2c78b3239ecfab21649";
static const char *tc2_sha512 =
    "164b7a7bfcf819e2e395fbe73b56e0a387bd64222e831fd610270cd7ea250554"
    "9758bf75c05a994a6d034f65f8f0e6fdcaeab1a34d4a6b4b636e070a38bce737";

/* RFC 4231 Test Case 2 SHA-256 binary digest */
static const guint8 tc2_sha256_bin[] = {
    0x5b, 0xdc, 0xc1, 0x46, 0xbf, 0x60, 0x75, 0x4e,
    0x6a, 0x04, 0x24, 0x26, 0x08, 0x95, 0x75, 0xc7,
    0x5a, 0x00, 0x3f, 0x08, 0x9d, 0x27, 0x39, 0x83,
    0x9d, 0xec, 0x58, 0xb9, 0x64, 0xec, 0x38, 0x43
};

/*
 * Test 1: HMAC-SHA256 one-shot string API
 */
static void
test_sha256_one_shot(void)
{
    gchar *result = g_compute_hmac_for_string(
        G_CHECKSUM_SHA256,
        (const guchar *)tc2_key, strlen(tc2_key),
        tc2_data, -1);
    check_str("HMAC-SHA256 one-shot (g_compute_hmac_for_string)", tc2_sha256, result);
    g_free(result);
}

/*
 * Test 2: HMAC-SHA384 one-shot
 */
static void
test_sha384_one_shot(void)
{
    gchar *result = g_compute_hmac_for_string(
        G_CHECKSUM_SHA384,
        (const guchar *)tc2_key, strlen(tc2_key),
        tc2_data, -1);
    check_str("HMAC-SHA384 one-shot", tc2_sha384, result);
    g_free(result);
}

/*
 * Test 3: HMAC-SHA512 one-shot
 */
static void
test_sha512_one_shot(void)
{
    gchar *result = g_compute_hmac_for_string(
        G_CHECKSUM_SHA512,
        (const guchar *)tc2_key, strlen(tc2_key),
        tc2_data, -1);
    check_str("HMAC-SHA512 one-shot", tc2_sha512, result);
    g_free(result);
}

/*
 * Test 4: g_compute_hmac_for_data (binary input, not string)
 */
static void
test_hmac_for_data(void)
{
    const guchar data[] = "what do ya want for nothing?";
    gchar *result = g_compute_hmac_for_data(
        G_CHECKSUM_SHA256,
        (const guchar *)tc2_key, strlen(tc2_key),
        data, 28);
    check_str("HMAC-SHA256 via g_compute_hmac_for_data", tc2_sha256, result);
    g_free(result);
}

/*
 * Test 5: Streaming API - single update
 */
static void
test_streaming_single_update(void)
{
    GHmac *hmac = g_hmac_new(G_CHECKSUM_SHA256,
                             (const guchar *)tc2_key, strlen(tc2_key));
    if (hmac == NULL) {
        fprintf(stderr, "FAIL [streaming single update]: g_hmac_new returned NULL\n");
        failures++;
        return;
    }

    g_hmac_update(hmac, (const guchar *)tc2_data, -1);
    const gchar *result = g_hmac_get_string(hmac);
    check_str("streaming API - single update", tc2_sha256, result);

    /* Calling get_string again should return the same cached value */
    const gchar *result2 = g_hmac_get_string(hmac);
    if (result != result2) {
        fprintf(stderr, "FAIL [streaming single update]: g_hmac_get_string not idempotent\n");
        failures++;
    } else {
        printf("PASS: g_hmac_get_string is idempotent\n");
    }

    g_hmac_unref(hmac);
}

/*
 * Test 6: Streaming API - multi-chunk update (5 chunks)
 */
static void
test_streaming_multi_chunk(void)
{
    GHmac *hmac = g_hmac_new(G_CHECKSUM_SHA256,
                             (const guchar *)tc2_key, strlen(tc2_key));
    if (hmac == NULL) {
        fprintf(stderr, "FAIL [multi-chunk]: g_hmac_new returned NULL\n");
        failures++;
        return;
    }

    /* "what do ya want for nothing?" split into 5 pieces */
    g_hmac_update(hmac, (const guchar *)"what ", 5);
    g_hmac_update(hmac, (const guchar *)"do ", 3);
    g_hmac_update(hmac, (const guchar *)"ya ", 3);
    g_hmac_update(hmac, (const guchar *)"want for ", 9);
    g_hmac_update(hmac, (const guchar *)"nothing?", 8);

    const gchar *result = g_hmac_get_string(hmac);
    check_str("streaming API - 5-chunk update", tc2_sha256, result);
    g_hmac_unref(hmac);
}

/*
 * Test 7: g_hmac_copy - verify partial state is correctly duplicated
 */
static void
test_hmac_copy(void)
{
    GHmac *hmac = g_hmac_new(G_CHECKSUM_SHA256,
                             (const guchar *)tc2_key, strlen(tc2_key));
    if (hmac == NULL) {
        fprintf(stderr, "FAIL [copy]: g_hmac_new returned NULL\n");
        failures++;
        return;
    }

    /* Feed partial data, then copy */
    g_hmac_update(hmac, (const guchar *)"what do ya want ", 16);
    GHmac *copy = g_hmac_copy(hmac);

    /* Complete the copy with remaining data */
    g_hmac_update(copy, (const guchar *)"for nothing?", 12);
    const gchar *copy_result = g_hmac_get_string(copy);
    check_str("g_hmac_copy produces correct result", tc2_sha256, copy_result);

    /* The original should be independent - feed it different data */
    g_hmac_update(hmac, (const guchar *)"for nothing?", 12);
    const gchar *orig_result = g_hmac_get_string(hmac);
    check_str("original after copy is independent", tc2_sha256, orig_result);

    g_hmac_unref(copy);
    g_hmac_unref(hmac);
}

/*
 * Test 8: g_hmac_get_digest - binary output path
 */
static void
test_hmac_get_digest(void)
{
    GHmac *hmac = g_hmac_new(G_CHECKSUM_SHA256,
                             (const guchar *)tc2_key, strlen(tc2_key));
    if (hmac == NULL) {
        fprintf(stderr, "FAIL [get_digest]: g_hmac_new returned NULL\n");
        failures++;
        return;
    }

    g_hmac_update(hmac, (const guchar *)tc2_data, -1);

    guint8 digest[32];
    gsize digest_len = sizeof(digest);
    g_hmac_get_digest(hmac, digest, &digest_len);

    if (digest_len != 32) {
        fprintf(stderr, "FAIL [get_digest]: expected len 32, got %zu\n", digest_len);
        failures++;
    } else {
        check_digest("g_hmac_get_digest binary output", tc2_sha256_bin, digest, 32);
    }

    g_hmac_unref(hmac);
}

/*
 * Test 9: g_hmac_ref / g_hmac_unref lifecycle
 */
static void
test_hmac_ref_unref(void)
{
    GHmac *hmac = g_hmac_new(G_CHECKSUM_SHA256,
                             (const guchar *)tc2_key, strlen(tc2_key));
    if (hmac == NULL) {
        fprintf(stderr, "FAIL [ref/unref]: g_hmac_new returned NULL\n");
        failures++;
        return;
    }

    /* Take an extra ref */
    GHmac *ref = g_hmac_ref(hmac);
    if (ref != hmac) {
        fprintf(stderr, "FAIL [ref/unref]: g_hmac_ref returned different pointer\n");
        failures++;
    }

    g_hmac_update(hmac, (const guchar *)tc2_data, -1);
    const gchar *result = g_hmac_get_string(hmac);
    check_str("ref/unref: HMAC still works after ref", tc2_sha256, result);

    /* First unref should not free (refcount goes 2 -> 1) */
    g_hmac_unref(hmac);

    /* Verify the ref'd copy still works */
    const gchar *result2 = g_hmac_get_string(ref);
    check_str("ref/unref: still valid after first unref", tc2_sha256, result2);

    /* Final unref frees */
    g_hmac_unref(ref);

    printf("PASS: ref/unref lifecycle completed without crash\n");
}

/*
 * Test 10: Empty data
 */
static void
test_empty_data(void)
{
    /* HMAC-SHA256("Jefe", "") - computed independently */
    const char *expected =
        "923598ca6d64af2a5dba79dcd021a8a0fe5c5f557519adaaf0ad532d4506dd30";

    gchar *result = g_compute_hmac_for_string(
        G_CHECKSUM_SHA256,
        (const guchar *)tc2_key, strlen(tc2_key),
        "", 0);
    check_str("HMAC-SHA256 with empty data", expected, result);
    g_free(result);
}

/*
 * Test 11: RFC 4231 Test Case 1 (different key/data than TC2)
 * Key = 0x0b repeated 20 times, data = "Hi There"
 */
static void
test_rfc4231_tc1(void)
{
    const guint8 key[20] = {
        0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b,
        0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b,
        0x0b, 0x0b, 0x0b, 0x0b
    };
    const char *expected_sha256 =
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7";

    gchar *result = g_compute_hmac_for_string(
        G_CHECKSUM_SHA256,
        key, 20,
        "Hi There", 8);
    check_str("RFC 4231 TC1 SHA-256 (binary key)", expected_sha256, result);
    g_free(result);
}

int main(void) {
    test_sha256_one_shot();       /* Test 1 */
    test_sha384_one_shot();       /* Test 2 */
    test_sha512_one_shot();       /* Test 3 */
    test_hmac_for_data();         /* Test 4 */
    test_streaming_single_update(); /* Test 5 */
    test_streaming_multi_chunk(); /* Test 6 */
    test_hmac_copy();             /* Test 7 */
    test_hmac_get_digest();       /* Test 8 */
    test_hmac_ref_unref();        /* Test 9 */
    test_empty_data();            /* Test 10 */
    test_rfc4231_tc1();           /* Test 11 */

    printf("\n%d test(s) failed\n", failures);
    return failures > 0 ? 1 : 0;
}
