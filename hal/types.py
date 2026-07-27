"""
HAL 类型定义 (对应 host_types.h, hal_os.h 类型部分)
"""

import ctypes
import enum


# ── 基础类型 ──────────────────────────────────────────────────
HostOsSem     = int   # 信号量句柄 (内部引用计数)
HostOsThread  = int   # 线程句柄


# ── 错误码 ────────────────────────────────────────────────────
class ErrCode(enum.IntEnum):
    SUCCESS         = 0
    FAIL            = -1
    TIMEOUT         = -2
    INVALID_PARAM   = -3
    BUSY            = -4
    NO_MEMORY       = -5
    NOT_SUPPORTED   = -6
    ALREADY_EXISTS  = -7
    NOT_FOUND       = -8
    CHECK_FAILED    = -9

ERRCODE_SUCCESS = ErrCode.SUCCESS


# ── IO 枚举 ────────────────────────────────────────────────��──
class IoMode(enum.IntEnum):
    DISABLE   = 0
    OUTPUT    = 1
    INPUT     = 2
    INTERRUPT = 3

class IoPull(enum.IntEnum):
    NO   = 0
    UP   = 1
    DOWN = 2

class IoValue(enum.IntEnum):
    LOW  = 0
    HIGH = 1

class IoIrqTrig(enum.IntEnum):
    RISING  = 0
    FALLING = 1
    BOTH    = 2
    HIGH    = 3
    LOW     = 4


# ── COM 枚举 ──────────────────────────────────────────────────
class ComType(enum.IntEnum):
    SPI  = 0
    I2C  = 1
    UART = 2

class UploadType(enum.IntEnum):
    ACTIVE       = 0   # 设备主动上传 (如 UART)
    PASSIVE      = 1   # Host 轮询读取 (SPI/I2C)
    ACTIVE_DELAY = 2

class NotifyType(enum.IntEnum):
    EDGE             = 0   # GPIO 边沿通知
    COM_ISR          = 1   # COM 层中断 ISR
    COM_IRQ_THREAD   = 2   # COM 层中断线程
