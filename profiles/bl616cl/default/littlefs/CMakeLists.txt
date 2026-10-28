# Only the pinned upstream engine. No SDK port, KV or autoformat.
sdk_generate_library(arduino_littlefs)
set(LFS_DIR $ENV{BL_SDK_BASE}/components/fs/littlefs/littlefs)
sdk_library_add_sources(${LFS_DIR}/lfs.c ${LFS_DIR}/lfs_util.c)
sdk_add_include_directories(${LFS_DIR})
