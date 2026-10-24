/* TLS 根证书束（ticket 0029）：构建时由 SDK 的 gen_crt_bundle.py 从 cacrt_all.pem 全部证书生成，
 * 格式见 bl_crt_bundle。放在 flash，只在程序引用 arduino_crt_bundle 时链入。 */
    .section .rodata.arduino_crt_bundle, "a"
    .balign 4
    .global arduino_crt_bundle
    .type arduino_crt_bundle, %object
arduino_crt_bundle:
    .incbin "arduino_crt_bundle.bin"
arduino_crt_bundle_end:
    .size arduino_crt_bundle, . - arduino_crt_bundle

    .balign 4
    .global arduino_crt_bundle_size
    .type arduino_crt_bundle_size, %object
arduino_crt_bundle_size:
    .word arduino_crt_bundle_end - arduino_crt_bundle
    .size arduino_crt_bundle_size, 4
