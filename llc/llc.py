"""
LLC 层 — 逻辑链路控制层 (对应 host_llc.c/.h)

职责:
  1. 设备生命周期: Regist/Open/Close/UnRegist
  2. 数据收发 (带总线占用管理)
  3. IO 控制 (UPD/RST/Notify 引脚)
  4. 通知回调管理
  5. 接收状态机: IDLE → CLEAN → CACHE → DIRECT → IDLE
  6. 缓冲区管理 (零拷贝/缓存)
"""

import logging
import time
from typing import Callable, Optional
from dataclasses import dataclass, field

from bus import HostBusDevice, DevHw
from hal import (
    sem_create, sem_delete, sem_take, sem_give,
    enter_critical, exit_critical,
    io_mode_set, io_write, io_read, io_irq_cfg, io_irq_enable, io_irq_disable,
    delay_ms,
)
from hal.types import IoMode, IoValue, IoIrqTrig, UploadType, NotifyType
from config import CFG_LLC_DEVICE_RX_CACHE_SIZE, CFG_BUFFER_TIMEOUT_MS

logger = logging.getLogger("llc")


# ── 接收状态机枚举 ───────────────────────────────────────────
class RxState:
    NONE        = 0
    IDLE        = 1    # 空闲, 等待通知
    CLEAN       = 2    # 清空缓存, 准备接收
    CACHE       = 3    # 缓存接收数据
    DIRECT      = 4    # 直接写入用户缓冲区


# ── 通知回调类型 ─────────────────────────────────────────────
NotifyCB = Callable[["LLCHandle"], None]        # 通知回调
BufferGetCB = Callable[["LLCHandle", int], Optional[memoryview]]  # 缓冲区获取回调


# ── LLC Handle ────────────────────────────────────────────────
@dataclass
class LLCHandle:
    """LLC 设备句柄 (对应 LLC_HANDLE_S)"""

    device: Optional[HostBusDevice] = None

    # 硬件配置副本
    hw: Optional[DevHw] = None

    # 通知
    notify_enable: bool = False
    notify_cb: Optional[NotifyCB] = None
    notify_arg: any = None

    # 缓冲区
    buffer_get_cb: Optional[BufferGetCB] = None
    buffer_get_arg: any = None
    buffer: Optional[bytearray] = None          # 用户缓冲区
    buffer_size: int = 0
    buffer_data_len: int = 0
    buffer_from_alloc: bool = False
    is_getting_buffer: bool = False
    buffer_timeout: int = CFG_BUFFER_TIMEOUT_MS
    buffer_complete_sem: int = 0                # 信号量 ID

    # 接收缓存
    rx_cache: Optional[bytearray] = None
    rx_cache_size: int = CFG_LLC_DEVICE_RX_CACHE_SIZE
    rx_cache_data_len: int = 0
    rx_cache_data_offset: int = 0

    # 状态机
    rx_state: int = RxState.NONE
    active_upload_int_mode: str = "NONE"  # NONE → IDLE → CLEAN → CACHE → DIRECT

    def open(self) -> int:
        """打开设备 (对应 LLC_Device_Open)"""

        if self.device is None or self.hw is None:
            return -1

        hw = self.hw
        bus = self.device._bus
        if bus is None:
            return -1

        # 1. 打开总线设备
        bus.device_open(self.device.virtual_id)

        # 2. 配置 RST IO
        if hw.rst_io:
            io_mode_set(hw.rst_io, IoMode.OUTPUT)
            io_write(hw.rst_io, IoValue.HIGH)  # 拉高复位
            delay_ms(1)

        # 3. 配置 Notify IO (PASSIVE 模式需要)
        if hw.upload_type == UploadType.PASSIVE:
            io_mode_set(hw.notify_io, IoMode.INPUT, pull="down")

        # 4. 启用通知中断 (PASSIVE)
        if hw.upload_type == UploadType.PASSIVE:
            io_irq_cfg(hw.notify_io, IoIrqTrig.RISING)
            io_irq_enable(hw.notify_io)
            self.notify_enable = True

        self.device.working = True
        self.rx_state = RxState.IDLE
        logger.info(f"LLC device opened: vid=0x{self.device.virtual_id:04X}")
        return 0

    def close(self) -> int:
        """关闭设备 (对应 LLC_Device_Close)"""

        if self.device is None or self.hw is None:
            return -1

        hw = self.hw

        # 1. 禁用通知中断
        if hw.upload_type == UploadType.PASSIVE:
            io_irq_disable(hw.notify_io)
            self.notify_enable = False

        # 2. 拉低复位
        if hw.rst_io:
            io_write(hw.rst_io, IoValue.LOW)

        # 3. 关闭总线设备
        bus = self.device._bus
        if bus:
            bus.device_close(self.device.virtual_id)

        self.device.working = False
        self.rx_state = RxState.NONE
        logger.info(f"LLC device closed: vid=0x{self.device.virtual_id:04X}")
        return 0

    # ── 数据收发 ──────────────────────────────────────────

    def send(self, data: bytes, timeout_ms: int = 1000) -> int:
        """发送数据 (对应 LLC_Send)

        占用总线 → 发送数据 → 释放总线
        """
        if self.device is None or self.device._bus is None:
            return -1

        bus = self.device._bus
        if not bus.take_right(self.device.virtual_id, timeout_ms):
            logger.warning(f"LLC send: bus busy, timeout={timeout_ms}ms")
            return -1

        try:
            n = bus.write(self.device.virtual_id, data)
            return n
        finally:
            bus.release_right(self.device.virtual_id)

    def recv(self, size: int, timeout_ms: int = 1000) -> bytes:
        """接收数据 (对应 LLC_Recv)

        占用总线 → 读取数据 → 释放总线
        """
        if self.device is None or self.device._bus is None:
            return b""

        bus = self.device._bus
        start = time.monotonic()

        while (time.monotonic() - start) * 1000 < timeout_ms:
            if bus.take_right(self.device.virtual_id, 10):
                try:
                    data = bus.read(self.device.virtual_id, size)
                    if data:
                        return data
                finally:
                    bus.release_right(self.device.virtual_id)
            time.sleep(0.001)

        return b""

    # ── IO 控制 ───────────────────────────────────────────

    def io_set_rst(self, value: IoValue) -> int:
        if self.hw and self.hw.rst_io:
            return io_write(self.hw.rst_io, value)
        return -1

    def io_enable_rst(self) -> int:
        if self.hw and self.hw.rst_io:
            return io_mode_set(self.hw.rst_io, IoMode.OUTPUT)
        return -1

    def io_get_notify(self) -> IoValue:
        if self.hw:
            return io_read(self.hw.notify_io)
        return IoValue.LOW

    def io_notify_irq_control(self, enable: bool) -> int:
        if enable:
            return io_irq_enable(self.hw.notify_io) if self.hw else -1
        return io_irq_disable(self.hw.notify_io) if self.hw else -1

    # ── 通知回调 ──────────────────────────────────────────

    def notify_handle_regist(self, cb: NotifyCB, arg: any = None) -> int:
        """注册通知回调 (TL 层注册)"""
        self.notify_cb = cb
        self.notify_arg = arg
        return 0

    def buffer_get_handle_regist(self, cb: BufferGetCB, arg: any = None) -> int:
        """注册缓冲区获取回调 (TL 层注册)"""
        self.buffer_get_cb = cb
        self.buffer_get_arg = arg
        return 0
