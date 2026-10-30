/*
 * Generate the TLS root bundle from all cacrt_all.pem certificates using SDK gen_crt_bundle.py.
 * Use bl_crt_bundle format, store in Flash, and link only when arduino_crt_bundle is referenced.
 */
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
