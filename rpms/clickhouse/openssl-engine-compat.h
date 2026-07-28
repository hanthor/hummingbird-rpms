#pragma once

/*
 * Fedora builds OpenSSL without the removed ENGINE API and therefore no
 * longer ships openssl/engine.h. Some bundled ClickHouse consumers include
 * that header unconditionally even though their ENGINE code is protected by
 * OPENSSL_NO_ENGINE. Keep those includes buildable until the dependencies
 * remove their legacy include.
 */
#ifndef OPENSSL_NO_ENGINE
#define OPENSSL_NO_ENGINE
#endif

#ifndef OPENSSL_INIT_ENGINE_ALL_BUILTIN
#define OPENSSL_INIT_ENGINE_ALL_BUILTIN 0
#endif
