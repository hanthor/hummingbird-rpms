/*
 * Negative test: verify that g_hmac_new() fails when libcrypto.so.3 is
 * unavailable. This proves the OpenSSL backend is actually in use —
 * if glib2 had been built with the built-in HMAC, this would still succeed
 * even without OpenSSL.
 *
 * This program is designed to be run AFTER libcrypto.so.3 has been
 * made unavailable (e.g. by renaming it).
 */
#include <glib.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const char *key = "Jefe";

    /* Suppress the expected g_warning from the OpenSSL init failure */
    g_log_set_handler("GLib", G_LOG_LEVEL_WARNING | G_LOG_FLAG_FATAL,
                      (GLogFunc)g_log_default_handler, NULL);

    GHmac *hmac = g_hmac_new(G_CHECKSUM_SHA256,
                             (const guchar *)key, strlen(key));
    if (hmac != NULL) {
        fprintf(stderr, "FAIL: g_hmac_new succeeded without libcrypto.so.3!\n");
        fprintf(stderr, "This means the OpenSSL backend is NOT being used.\n");
        g_hmac_unref(hmac);
        return 1;
    }

    printf("PASS: g_hmac_new correctly returned NULL without libcrypto.so.3\n");
    printf("This confirms the OpenSSL HMAC backend is in use.\n");
    return 0;
}
