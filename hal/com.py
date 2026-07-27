"""
HAL COM 通信抽象层 (对应 hal_com.h + port_spi.c/port_i2c.c/port_uart_*.c)

定义 host_com_ops_t 结构体的 Python 版本
提供 SPI/I2C/UART 的具体实现工厂
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable
import logging

from .types import ComType, NotifyType

logger = logging.getLogger("hal.com")


# ── ComOps: 通信操作接口 (对应 host_com_ops_t) ────────────────

class ComOps(ABC):
    """通信操作抽象接口"""

    com_type: ComType

    def init(self, bus_id: int, speed: int, param: int) -> int:
        """初始化总线"""
        return 0

    def deinit(self, bus_id: int) -> int:
        """反初始化总线"""
        return 0

    def open(self, bus_id: int, device_param: int) -> int:
        """打开设备"""
        return 0

    def close(self, bus_id: int) -> int:
        """关闭设备"""
        return 0

    @abstractmethod
    def write(self, bus_id: int, data: bytes) -> int:
        """写数据, 返回写入字节数"""
        ...

    @abstractmethod
    def read(self, bus_id: int, size: int) -> bytes:
        """读数据, 返回读取的字节"""
        ...

    def read_finish(self, bus_id: int) -> int:
        """停止读 (UART 需要)"""
        return 0

    def interrupt_ctrl(self, bus_id: int, enable: bool, threshold: int = 0) -> int:
        """中断控制 (设置接收阈值, 启用/禁用)"""
        return 0

    def interrupt_handle_regist(self, bus_id: int, callback: Callable) -> int:
        """注册数据可用中断处理"""
        return 0

    def data_callback_regist(self, bus_id: int, callback: Callable) -> int:
        """注册数据接收完成回调"""
        return 0


# ── SPI ComOps 实现 (对应 port_spi.c) ─────────────────────────

class ComOpsSpi(ComOps):
    """SPI 通信实现 (Linux spidev)

    device_param 用于传递 CS 引脚和模式:
      - 低 16 位: CS 引脚编号
      - 高 16 位: SPI 模式 (0~3)
    """

    com_type = ComType.SPI

    def __init__(self, device_path_tpl: str = "/dev/spidev{}.{}"):
        """
        Args:
            device_path_tpl: SPI 设备路径模板, 如 "/dev/spidev{}.{}"
                             bus_id 对应 SPI 总线号
        """
        import os as _os, struct, fcntl
        self._os = _os
        self._struct = struct
        self._fcntl = fcntl
        self._device_path_tpl = device_path_tpl
        self._fds: dict[int, int] = {}  # bus_id -> fd

    def init(self, bus_id: int, speed: int, param: int) -> int:
        """初始化 SPI 总线

        Args:
            bus_id: SPI 总线号 (e.g. 0 for spidev0.x)
            speed: 时钟频率 Hz
            param: SPI 模式 (0~3) 或 线数 (1/2/4)
        """
        device = self._device_path_tpl.format(bus_id, 0)
        try:
            fd = self._os.open(device, self._os.O_RDWR)
            self._fds[bus_id] = fd

            # 设置模式 (默认 MODE_0)
            mode = 0
            self._fcntl.ioctl(fd, 0x40016B01, self._struct.pack("B", mode))

            # 设置字长
            self._fcntl.ioctl(fd, 0x40016B03, self._struct.pack("B", 8))

            # 设置速度
            self._fcntl.ioctl(fd, 0x40046B04, self._struct.pack("<I", speed))

            logger.info(f"SPI bus[{bus_id}] init: {device}, speed={speed}Hz")
            return 0
        except OSError as e:
            logger.error(f"SPI bus[{bus_id}] init failed: {e}")
            return -1

    def deinit(self, bus_id: int) -> int:
        fd = self._fds.pop(bus_id, None)
        if fd is not None:
            self._os.close(fd)
        return 0

    def open(self, bus_id: int, device_param: int) -> int:
        return 0  # CS 由 spidev 内核驱动管理

    def close(self, bus_id: int) -> int:
        return 0

    def write(self, bus_id: int, data: bytes) -> int:
        fd = self._fds.get(bus_id)
        if fd is None:
            return -1
        try:
            return self._os.write(fd, data)
        except OSError:
            return -1

    def read(self, bus_id: int, size: int) -> bytes:
        fd = self._fds.get(bus_id)
        if fd is None:
            return b""
        try:
            return self._os.read(fd, size)
        except OSError:
            return b""


# ── I2C / UART ComOps (Stub) ──────────────────────────────────

class ComOpsI2c(ComOps):
    com_type = ComType.I2C

    def write(self, bus_id: int, data: bytes) -> int:
        return -1  # TODO

    def read(self, bus_id: int, size: int) -> bytes:
        return b""


class ComOpsUart(ComOps):
    com_type = ComType.UART

    def write(self, bus_id: int, data: bytes) -> int:
        return -1  # TODO

    def read(self, bus_id: int, size: int) -> bytes:
        return b""


# ── ComOps 工厂 ───────────────────────────────────────────────

_COM_OPS_FACTORY: dict[ComType, type[ComOps]] = {
    ComType.SPI:  ComOpsSpi,
    ComType.I2C:  ComOpsI2c,
    ComType.UART: ComOpsUart,
}


def create_com_ops(com_type: ComType, **kwargs) -> ComOps:
    """创建 COM 操作实例

    Args:
        com_type: 总线类型
        **kwargs: 传给构造函数 (如 SPI 的 device_path_tpl)
    """
    cls = _COM_OPS_FACTORY.get(com_type)
    if cls is None:
        raise ValueError(f"Unknown bus type: {com_type}")
    return cls(**kwargs)


def get_com_ops_by_type(bus_type: int) -> ComOps:
    """根据总线类型枚举获取对应 ComOps (向后兼容)"""
    return create_com_ops(ComType(bus_type))
