# Invoked by build.sh; BL_SDK_BASE must point to the builder-owned SDK checkout.
SDK_DEMO_PATH ?= $(abspath .)
CHIP ?= bl616cl
BOARD ?= bl616cldk
ifndef BL_SDK_BASE
$(error BL_SDK_BASE is not set)
endif

export BL_SDK_BASE

include $(BL_SDK_BASE)/project.build
