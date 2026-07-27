"""
HAL 层 — 硬件抽象层 (对应官方 porting/include/)

统一所有平台相关的底层 API:
  hal/os.py   — 线程/信号量/延时/内存
  hal/io.py   — GPIO 引脚读写/中断
  hal/com.py  — SPI/I2C/UART 通信操作
  hal/log.py  — 分级日志
  hal/store.py — 存储
"""

import sys
from . import os as _os
from . import io as _io
from . import com as _com
from . import log as _log
from . import store as _store

from .types import (
    HostOsSem, HostOsThread,
    IoMode, IoPull, IoValue, IoIrqTrig,
    ComType, UploadType, NotifyType,
    ERRCODE_SUCCESS,
)


# ── OS 抽象 ──────────────────────────────────────────────────
enter_critical     = _os.enter_critical
exit_critical      = _os.exit_critical
timestamp_us       = _os.timestamp_us
delay_ms           = _os.delay_ms
delay_us           = _os.delay_us
malloc             = _os.malloc
free               = _os.free
mem_set            = _os.mem_set
mem_copy           = _os.mem_copy
mem_cmp            = _os.mem_cmp
thread_create      = _os.thread_create
thread_delete      = _os.thread_delete
sem_create         = _os.sem_create
sem_delete         = _os.sem_delete
sem_take           = _os.sem_take
sem_give           = _os.sem_give
pm_lock            = _os.pm_lock
pm_unlock          = _os.pm_unlock

# ── IO 抽象 ──────────────────────────────────────────────────
io_mode_set        = _io.mode_set
io_write           = _io.write
io_read            = _io.read
io_irq_cfg         = _io.irq_cfg
io_irq_enable      = _io.irq_enable
io_irq_disable     = _io.irq_disable

# ── COM 抽象 ─────────────────────────────────────────────────
com_type_spi       = _com.ComType.SPI
com_type_i2c       = _com.ComType.I2C
com_type_uart      = _com.ComType.UART

# ── Log ──────────────────────────────────────────────────────
log_print          = _log.print_log
log_d              = _log.debug
log_i              = _log.info
log_w              = _log.warning
log_e              = _log.error
