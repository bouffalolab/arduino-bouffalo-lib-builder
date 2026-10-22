# 由 build.sh 调用；BL_SDK_BASE 必须指向 builder 检出的 SDK。
SDK_DEMO_PATH ?= $(abspath .)
CHIP ?= bl616cl
BOARD ?= bl616cldk
ifndef BL_SDK_BASE
$(error BL_SDK_BASE is not set)
endif

export BL_SDK_BASE

include $(BL_SDK_BASE)/project.build
