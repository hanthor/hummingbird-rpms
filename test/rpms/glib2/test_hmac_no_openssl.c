/*
 * This file is no longer used.  It previously verified that g_hmac_new()
 * fails when libcrypto.so.3 is unavailable (proving the OpenSSL backend
 * was in use).  With the FIPS stub approach, g_hmac_new() always returns
 * NULL regardless of OpenSSL availability, so this test is obsolete.
 *
 * Kept as a placeholder so that git history shows the intent.
 * The active test is test_hmac.c.
 */
#include <stdio.h>

int main(void) {
    printf("SKIP: test_hmac_no_openssl is no longer applicable\n");
    return 0;
}
