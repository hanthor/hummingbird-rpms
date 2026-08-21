#include <curl/curl.h>

int main(int argc, char **argv) {
    CURL *handle;
    CURLcode result;

    if (argc != 2 || curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) {
        return 2;
    }
    handle = curl_easy_init();
    if (handle == NULL) {
        curl_global_cleanup();
        return 3;
    }
    curl_easy_setopt(handle, CURLOPT_URL, argv[1]);
    curl_easy_setopt(handle, CURLOPT_FAILONERROR, 1L);
    result = curl_easy_perform(handle);
    curl_easy_cleanup(handle);
    curl_global_cleanup();
    return result == CURLE_OK ? 0 : 4;
}
