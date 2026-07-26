# Third-party components

This project interoperates with, builds against, or incorporates code from:

- OpenVPN Community 2.7.5-I001. Official installers are downloaded from
  OpenVPN and verified by pinned SHA-256 plus Authenticode or OpenPGP
  signatures. They are not redistributed here.
- OpenSSL 3.6.3. The Windows provider targets the exact `libcrypto` ABI bundled
  by the pinned OpenVPN installer.
- Crypto++ 8.9.0. Its official source tag is SHA-256 pinned and linked
  statically into the LEA provider under the Crypto++ Boost Software License.
- `golang.org/x/sys` and `golang.org/x/term`, as recorded in `portable/go.sum`.

Build manifests and workflow logs record the exact source and installer hashes.
The SecuwaySSL gateway protocol implementation is independent and is not an
official Secuwiz product.
