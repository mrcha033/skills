#include <openssl/evp.h>
#include <openssl/provider.h>
#include <openssl/err.h>
#include <openssl/crypto.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    static const unsigned char key[16] = {
        0x87, 0xF1, 0x42, 0x4F, 0x1A, 0x14, 0x83, 0xCC,
        0x1F, 0xD0, 0x35, 0x4E, 0x18, 0xA9, 0x94, 0xAB
    };
    static const unsigned char iv[16] = {
        0xCF, 0x58, 0x4E, 0x6E, 0xF6, 0xD6, 0x42, 0x88,
        0x0A, 0xB7, 0x87, 0x42, 0x7D, 0xB9, 0xB0, 0x76
    };
    static const unsigned char plaintext[16] = {
        0x13, 0x9D, 0x4E, 0xFF, 0x8D, 0x35, 0xB7, 0x6E,
        0x85, 0xBF, 0x06, 0xFE, 0x99, 0x71, 0x63, 0xCB
    };
    static const unsigned char expected_ciphertext[16] = {
        0x49, 0xB9, 0xF3, 0x22, 0x6D, 0xA5, 0x4B, 0x4A,
        0x0D, 0x38, 0x5A, 0x9C, 0x48, 0x70, 0x52, 0x4B
    };
    unsigned char ciphertext[32] = {0};
    unsigned char recovered[32] = {0};
    int out_len = 0;
    int final_len = 0;
    int recovered_len = 0;
    int recovered_final_len = 0;
    int rc = 1;

    OSSL_PROVIDER *lea = OSSL_PROVIDER_load(NULL, "lea");
    OSSL_PROVIDER *def = OSSL_PROVIDER_load(NULL, "default");
    printf("providers lea=%p default=%p\n", (void *)lea, (void *)def);
    if (!lea || !def) {
        ERR_print_errors_fp(stderr);
        goto cleanup;
    }

    EVP_CIPHER *cipher = EVP_CIPHER_fetch(NULL, "LEA-128-CBC", NULL);
    printf("cipher=%p\n", (void *)cipher);
    if (!cipher) {
        ERR_print_errors_fp(stderr);
        goto cleanup;
    }

    printf("block=%d key=%d iv=%d mode=%d flags=%lx\n",
           EVP_CIPHER_get_block_size(cipher), EVP_CIPHER_get_key_length(cipher),
           EVP_CIPHER_get_iv_length(cipher), EVP_CIPHER_get_mode(cipher),
           EVP_CIPHER_get_flags(cipher));

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx ||
        !EVP_EncryptInit_ex2(ctx, cipher, key, iv, NULL) ||
        !EVP_CIPHER_CTX_set_padding(ctx, 0) ||
        !EVP_EncryptUpdate(ctx, ciphertext, &out_len, plaintext, sizeof(plaintext)) ||
        !EVP_EncryptFinal_ex(ctx, ciphertext + out_len, &final_len)) {
        ERR_print_errors_fp(stderr);
        goto cleanup_ctx;
    }
    if (out_len + final_len != (int)sizeof(expected_ciphertext) ||
        CRYPTO_memcmp(ciphertext, expected_ciphertext, sizeof(expected_ciphertext)) != 0) {
        fprintf(stderr, "LEA-128-CBC encryption KAT failed\n");
        goto cleanup_ctx;
    }

    EVP_CIPHER_CTX_reset(ctx);
    if (!EVP_DecryptInit_ex2(ctx, cipher, key, iv, NULL) ||
        !EVP_CIPHER_CTX_set_padding(ctx, 0) ||
        !EVP_DecryptUpdate(ctx, recovered, &recovered_len,
                           expected_ciphertext, sizeof(expected_ciphertext)) ||
        !EVP_DecryptFinal_ex(ctx, recovered + recovered_len, &recovered_final_len)) {
        ERR_print_errors_fp(stderr);
        goto cleanup_ctx;
    }
    if (recovered_len + recovered_final_len != (int)sizeof(plaintext) ||
        CRYPTO_memcmp(recovered, plaintext, sizeof(plaintext)) != 0) {
        fprintf(stderr, "LEA-128-CBC decryption KAT failed\n");
        goto cleanup_ctx;
    }

    puts("LEA-128-CBC KAT encrypt=PASS decrypt=PASS");
    rc = 0;

cleanup_ctx:
    OPENSSL_cleanse(ciphertext, sizeof(ciphertext));
    OPENSSL_cleanse(recovered, sizeof(recovered));
    EVP_CIPHER_CTX_free(ctx);
    EVP_CIPHER_free(cipher);
cleanup:
    OSSL_PROVIDER_unload(def);
    OSSL_PROVIDER_unload(lea);
    return rc;
}
