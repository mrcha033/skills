#include <openssl/core.h>
#include <openssl/core_dispatch.h>
#include <openssl/core_names.h>
#include <openssl/evp.h>
#include <openssl/params.h>
#include <openssl/crypto.h>

#include "lea.h"
#include "modes.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>
#include <vector>

namespace {

constexpr size_t kBlockSize = 16;
constexpr size_t kKeySize = 16;
constexpr size_t kIVSize = 16;

struct LeaCbcContext {
    bool encrypting = true;
    bool padding = true;
    bool key_set = false;
    bool iv_set = false;
    unsigned char key[kKeySize]{};
    unsigned char iv[kIVSize]{};
    std::vector<unsigned char> pending;
    CryptoPP::CBC_Mode<CryptoPP::LEA>::Encryption encryption;
    CryptoPP::CBC_Mode<CryptoPP::LEA>::Decryption decryption;

    ~LeaCbcContext() {
        OPENSSL_cleanse(key, sizeof(key));
        OPENSSL_cleanse(iv, sizeof(iv));
        if (!pending.empty()) {
            OPENSSL_cleanse(pending.data(), pending.size());
        }
    }
};

void cleanse_pending(LeaCbcContext *ctx) {
    if (!ctx->pending.empty()) {
        OPENSSL_cleanse(ctx->pending.data(), ctx->pending.size());
        ctx->pending.clear();
    }
}

void *lea_newctx(void *) {
    return new (std::nothrow) LeaCbcContext();
}

void lea_freectx(void *vctx) {
    delete static_cast<LeaCbcContext *>(vctx);
}

void *lea_dupctx(void *vctx) {
    auto *src = static_cast<LeaCbcContext *>(vctx);
    auto *dst = new (std::nothrow) LeaCbcContext();
    if (dst == nullptr) return nullptr;
    dst->encrypting = src->encrypting;
    dst->padding = src->padding;
    dst->key_set = src->key_set;
    dst->iv_set = src->iv_set;
    std::memcpy(dst->key, src->key, kKeySize);
    std::memcpy(dst->iv, src->iv, kIVSize);
    dst->pending = src->pending;
    try {
        if (dst->key_set && dst->iv_set) {
            dst->encryption.SetKeyWithIV(dst->key, kKeySize, dst->iv, kIVSize);
            dst->decryption.SetKeyWithIV(dst->key, kKeySize, dst->iv, kIVSize);
        }
    } catch (...) {
        delete dst;
        return nullptr;
    }
    return dst;
}

int lea_init(void *vctx, const unsigned char *key, size_t keylen,
             const unsigned char *iv, size_t ivlen, bool encrypting,
             const OSSL_PARAM params[]) {
    auto *ctx = static_cast<LeaCbcContext *>(vctx);
    ctx->encrypting = encrypting;
    cleanse_pending(ctx);

    if (key != nullptr) {
        if (keylen != kKeySize) return 0;
        std::memcpy(ctx->key, key, kKeySize);
        ctx->key_set = true;
    }
    if (iv != nullptr) {
        if (ivlen != kIVSize) return 0;
        std::memcpy(ctx->iv, iv, kIVSize);
        ctx->iv_set = true;
    }
    if (ctx->key_set && ctx->iv_set) {
        try {
            if (encrypting) {
                ctx->encryption.SetKeyWithIV(ctx->key, kKeySize, ctx->iv, kIVSize);
            } else {
                ctx->decryption.SetKeyWithIV(ctx->key, kKeySize, ctx->iv, kIVSize);
            }
        } catch (...) {
            return 0;
        }
    }

    if (params != nullptr) {
        const OSSL_PARAM *p = OSSL_PARAM_locate_const(params, OSSL_CIPHER_PARAM_PADDING);
        if (p != nullptr) {
            unsigned int value = 0;
            if (!OSSL_PARAM_get_uint(p, &value)) return 0;
            ctx->padding = value != 0;
        }
    }
    return 1;
}

int lea_encrypt_init(void *ctx, const unsigned char *key, size_t keylen,
                     const unsigned char *iv, size_t ivlen,
                     const OSSL_PARAM params[]) {
    return lea_init(ctx, key, keylen, iv, ivlen, true, params);
}

int lea_decrypt_init(void *ctx, const unsigned char *key, size_t keylen,
                     const unsigned char *iv, size_t ivlen,
                     const OSSL_PARAM params[]) {
    return lea_init(ctx, key, keylen, iv, ivlen, false, params);
}

int lea_update(void *vctx, unsigned char *out, size_t *outl, size_t outsize,
               const unsigned char *in, size_t inl) {
    auto *ctx = static_cast<LeaCbcContext *>(vctx);
    *outl = 0;
    if (!ctx->key_set || !ctx->iv_set) return 0;
    if (inl != 0 && in == nullptr) return 0;

    try {
        if (inl != 0) {
            ctx->pending.insert(ctx->pending.end(), in, in + inl);
        }
        size_t process_len = (ctx->pending.size() / kBlockSize) * kBlockSize;
        if (!ctx->encrypting && ctx->padding && process_len == ctx->pending.size() && process_len >= kBlockSize) {
            process_len -= kBlockSize;
        }
        if (process_len == 0) return 1;
        if (out == nullptr || outsize < process_len) return 0;

        if (ctx->encrypting) {
            ctx->encryption.ProcessData(out, ctx->pending.data(), process_len);
            std::memcpy(ctx->iv, out + process_len - kBlockSize, kBlockSize);
        } else {
            unsigned char next_iv[kBlockSize];
            std::memcpy(next_iv, ctx->pending.data() + process_len - kBlockSize, kBlockSize);
            ctx->decryption.ProcessData(out, ctx->pending.data(), process_len);
            std::memcpy(ctx->iv, next_iv, kBlockSize);
            OPENSSL_cleanse(next_iv, sizeof(next_iv));
        }
        OPENSSL_cleanse(ctx->pending.data(), process_len);
        ctx->pending.erase(ctx->pending.begin(), ctx->pending.begin() + static_cast<std::ptrdiff_t>(process_len));
        *outl = process_len;
        return 1;
    } catch (...) {
        return 0;
    }
}

int lea_final(void *vctx, unsigned char *out, size_t *outl, size_t outsize) {
    auto *ctx = static_cast<LeaCbcContext *>(vctx);
    *outl = 0;
    try {
        if (ctx->encrypting) {
            if (!ctx->padding) return ctx->pending.empty() ? 1 : 0;
            const unsigned char pad = static_cast<unsigned char>(kBlockSize - (ctx->pending.size() % kBlockSize));
            ctx->pending.insert(ctx->pending.end(), pad, pad);
            if (out == nullptr || outsize < ctx->pending.size()) return 0;
            ctx->encryption.ProcessData(out, ctx->pending.data(), ctx->pending.size());
            std::memcpy(ctx->iv, out + ctx->pending.size() - kBlockSize, kBlockSize);
            *outl = ctx->pending.size();
            cleanse_pending(ctx);
            return 1;
        }

        if (ctx->pending.empty()) return ctx->padding ? 0 : 1;
        if ((ctx->pending.size() % kBlockSize) != 0) return 0;
        if (out == nullptr || outsize < ctx->pending.size()) return 0;
        unsigned char next_iv[kBlockSize];
        std::memcpy(next_iv, ctx->pending.data() + ctx->pending.size() - kBlockSize, kBlockSize);
        ctx->decryption.ProcessData(out, ctx->pending.data(), ctx->pending.size());
        std::memcpy(ctx->iv, next_iv, kBlockSize);
        OPENSSL_cleanse(next_iv, sizeof(next_iv));
        size_t result_len = ctx->pending.size();
        if (ctx->padding) {
            const unsigned char pad = out[result_len - 1];
            if (pad == 0 || pad > kBlockSize || pad > result_len) {
                OPENSSL_cleanse(out, result_len);
                return 0;
            }
            unsigned char mismatch = 0;
            for (size_t i = 0; i < pad; ++i) mismatch |= out[result_len - 1 - i] ^ pad;
            if (mismatch != 0) {
                OPENSSL_cleanse(out, result_len);
                return 0;
            }
            result_len -= pad;
        }
        *outl = result_len;
        cleanse_pending(ctx);
        return 1;
    } catch (...) {
        return 0;
    }
}

int lea_cipher(void *vctx, unsigned char *out, size_t *outl, size_t outsize,
               const unsigned char *in, size_t inl) {
    size_t first = 0, last = 0;
    if (!lea_update(vctx, out, &first, outsize, in, inl)) return 0;
    unsigned char *final_out = out == nullptr ? nullptr : out + first;
    if (!lea_final(vctx, final_out, &last, outsize - first)) return 0;
    *outl = first + last;
    return 1;
}

int lea_get_params(OSSL_PARAM params[]) {
    OSSL_PARAM *p;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_MODE);
    if (p != nullptr && !OSSL_PARAM_set_uint(p, EVP_CIPH_CBC_MODE)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_KEYLEN);
    if (p != nullptr && !OSSL_PARAM_set_size_t(p, kKeySize)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_IVLEN);
    if (p != nullptr && !OSSL_PARAM_set_size_t(p, kIVSize)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_BLOCK_SIZE);
    if (p != nullptr && !OSSL_PARAM_set_size_t(p, kBlockSize)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_AEAD);
    if (p != nullptr && !OSSL_PARAM_set_int(p, 0)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_CUSTOM_IV);
    if (p != nullptr && !OSSL_PARAM_set_int(p, 0)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_CTS);
    if (p != nullptr && !OSSL_PARAM_set_int(p, 0)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_TLS1_MULTIBLOCK);
    if (p != nullptr && !OSSL_PARAM_set_int(p, 0)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_HAS_RAND_KEY);
    if (p != nullptr && !OSSL_PARAM_set_int(p, 0)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_ENCRYPT_THEN_MAC);
    if (p != nullptr && !OSSL_PARAM_set_int(p, 0)) return 0;
    return 1;
}

const OSSL_PARAM *lea_gettable_params(void *) {
    static const OSSL_PARAM table[] = {
        OSSL_PARAM_uint(OSSL_CIPHER_PARAM_MODE, nullptr),
        OSSL_PARAM_size_t(OSSL_CIPHER_PARAM_KEYLEN, nullptr),
        OSSL_PARAM_size_t(OSSL_CIPHER_PARAM_IVLEN, nullptr),
        OSSL_PARAM_size_t(OSSL_CIPHER_PARAM_BLOCK_SIZE, nullptr),
        OSSL_PARAM_int(OSSL_CIPHER_PARAM_AEAD, nullptr),
        OSSL_PARAM_int(OSSL_CIPHER_PARAM_CUSTOM_IV, nullptr),
        OSSL_PARAM_int(OSSL_CIPHER_PARAM_CTS, nullptr),
        OSSL_PARAM_int(OSSL_CIPHER_PARAM_TLS1_MULTIBLOCK, nullptr),
        OSSL_PARAM_int(OSSL_CIPHER_PARAM_HAS_RAND_KEY, nullptr),
        OSSL_PARAM_int(OSSL_CIPHER_PARAM_ENCRYPT_THEN_MAC, nullptr),
        OSSL_PARAM_END
    };
    return table;
}

int lea_get_ctx_params(void *vctx, OSSL_PARAM params[]) {
    auto *ctx = static_cast<LeaCbcContext *>(vctx);
    OSSL_PARAM *p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_KEYLEN);
    if (p != nullptr && !OSSL_PARAM_set_size_t(p, kKeySize)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_IVLEN);
    if (p != nullptr && !OSSL_PARAM_set_size_t(p, kIVSize)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_PADDING);
    if (p != nullptr && !OSSL_PARAM_set_uint(p, ctx->padding ? 1U : 0U)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_NUM);
    if (p != nullptr && !OSSL_PARAM_set_uint(p, 0)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_IV);
    if (p != nullptr && !OSSL_PARAM_set_octet_string(p, ctx->iv, kIVSize)) return 0;
    p = OSSL_PARAM_locate(params, OSSL_CIPHER_PARAM_UPDATED_IV);
    if (p != nullptr && !OSSL_PARAM_set_octet_string(p, ctx->iv, kIVSize)) return 0;
    return 1;
}

const OSSL_PARAM *lea_gettable_ctx_params(void *) {
    static const OSSL_PARAM table[] = {
        OSSL_PARAM_size_t(OSSL_CIPHER_PARAM_KEYLEN, nullptr),
        OSSL_PARAM_size_t(OSSL_CIPHER_PARAM_IVLEN, nullptr),
        OSSL_PARAM_uint(OSSL_CIPHER_PARAM_PADDING, nullptr),
        OSSL_PARAM_uint(OSSL_CIPHER_PARAM_NUM, nullptr),
        OSSL_PARAM_octet_string(OSSL_CIPHER_PARAM_IV, nullptr, 0),
        OSSL_PARAM_octet_string(OSSL_CIPHER_PARAM_UPDATED_IV, nullptr, 0),
        OSSL_PARAM_END
    };
    return table;
}

int lea_set_ctx_params(void *vctx, const OSSL_PARAM params[]) {
    auto *ctx = static_cast<LeaCbcContext *>(vctx);
    if (params == nullptr) return 1;
    const OSSL_PARAM *p = OSSL_PARAM_locate_const(params, OSSL_CIPHER_PARAM_PADDING);
    if (p != nullptr) {
        unsigned int value = 0;
        if (!OSSL_PARAM_get_uint(p, &value)) return 0;
        ctx->padding = value != 0;
    }
    return 1;
}

const OSSL_PARAM *lea_settable_ctx_params(void *) {
    static const OSSL_PARAM table[] = {
        OSSL_PARAM_uint(OSSL_CIPHER_PARAM_PADDING, nullptr),
        OSSL_PARAM_END
    };
    return table;
}

const OSSL_DISPATCH lea_cipher_functions[] = {
    {OSSL_FUNC_CIPHER_NEWCTX, reinterpret_cast<void (*)(void)>(lea_newctx)},
    {OSSL_FUNC_CIPHER_FREECTX, reinterpret_cast<void (*)(void)>(lea_freectx)},
    {OSSL_FUNC_CIPHER_DUPCTX, reinterpret_cast<void (*)(void)>(lea_dupctx)},
    {OSSL_FUNC_CIPHER_ENCRYPT_INIT, reinterpret_cast<void (*)(void)>(lea_encrypt_init)},
    {OSSL_FUNC_CIPHER_DECRYPT_INIT, reinterpret_cast<void (*)(void)>(lea_decrypt_init)},
    {OSSL_FUNC_CIPHER_UPDATE, reinterpret_cast<void (*)(void)>(lea_update)},
    {OSSL_FUNC_CIPHER_FINAL, reinterpret_cast<void (*)(void)>(lea_final)},
    {OSSL_FUNC_CIPHER_CIPHER, reinterpret_cast<void (*)(void)>(lea_cipher)},
    {OSSL_FUNC_CIPHER_GET_PARAMS, reinterpret_cast<void (*)(void)>(lea_get_params)},
    {OSSL_FUNC_CIPHER_GETTABLE_PARAMS, reinterpret_cast<void (*)(void)>(lea_gettable_params)},
    {OSSL_FUNC_CIPHER_GET_CTX_PARAMS, reinterpret_cast<void (*)(void)>(lea_get_ctx_params)},
    {OSSL_FUNC_CIPHER_GETTABLE_CTX_PARAMS, reinterpret_cast<void (*)(void)>(lea_gettable_ctx_params)},
    {OSSL_FUNC_CIPHER_SET_CTX_PARAMS, reinterpret_cast<void (*)(void)>(lea_set_ctx_params)},
    {OSSL_FUNC_CIPHER_SETTABLE_CTX_PARAMS, reinterpret_cast<void (*)(void)>(lea_settable_ctx_params)},
    {0, nullptr}
};

const OSSL_ALGORITHM lea_algorithms[] = {
    {"LEA-128-CBC:LEA128-CBC:LEA-CBC", "provider=lea", lea_cipher_functions, "LEA 128-bit CBC cipher"},
    {nullptr, nullptr, nullptr, nullptr}
};

const OSSL_ALGORITHM *provider_query(void *, int operation_id, int *no_cache) {
    *no_cache = 0;
    return operation_id == OSSL_OP_CIPHER ? lea_algorithms : nullptr;
}

int provider_get_params(void *, OSSL_PARAM params[]) {
    OSSL_PARAM *p = OSSL_PARAM_locate(params, OSSL_PROV_PARAM_NAME);
    if (p != nullptr && !OSSL_PARAM_set_utf8_ptr(p, "Secuway Native LEA Provider")) return 0;
    p = OSSL_PARAM_locate(params, OSSL_PROV_PARAM_VERSION);
    if (p != nullptr && !OSSL_PARAM_set_utf8_ptr(p, "0.1.0")) return 0;
    p = OSSL_PARAM_locate(params, OSSL_PROV_PARAM_STATUS);
    if (p != nullptr && !OSSL_PARAM_set_int(p, 1)) return 0;
    return 1;
}

const OSSL_PARAM *provider_gettable_params(void *) {
    static const OSSL_PARAM table[] = {
        OSSL_PARAM_utf8_ptr(OSSL_PROV_PARAM_NAME, nullptr, 0),
        OSSL_PARAM_utf8_ptr(OSSL_PROV_PARAM_VERSION, nullptr, 0),
        OSSL_PARAM_int(OSSL_PROV_PARAM_STATUS, nullptr),
        OSSL_PARAM_END
    };
    return table;
}

const OSSL_DISPATCH provider_functions[] = {
    {OSSL_FUNC_PROVIDER_QUERY_OPERATION, reinterpret_cast<void (*)(void)>(provider_query)},
    {OSSL_FUNC_PROVIDER_GET_PARAMS, reinterpret_cast<void (*)(void)>(provider_get_params)},
    {OSSL_FUNC_PROVIDER_GETTABLE_PARAMS, reinterpret_cast<void (*)(void)>(provider_gettable_params)},
    {0, nullptr}
};

}  // namespace

extern "C" int OSSL_provider_init(const OSSL_CORE_HANDLE *, const OSSL_DISPATCH *,
                                  const OSSL_DISPATCH **out, void **provctx) {
    *provctx = nullptr;
    *out = provider_functions;
    return 1;
}
