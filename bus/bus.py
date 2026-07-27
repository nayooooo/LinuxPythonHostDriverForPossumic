"""
COM Bus 总线层 (对应 host_llc_com_bus.h/.c)

管理总线树 (按 ComType 分 SPI/I2C/UART 三棵树),
负责总线的初始化、设备挂载、使用权仲裁
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from hal import sem_create, sem_delete, sem_take, sem_give
from hal.com import ComOps, get_com_ops_by_type
from hal.types import ComType

logger = logging.getLogger("bus")


# ── 总线事件方法 ────────────────────────────────────────────
class BusEventMethod:
    """总线事件调度策略"""
    ORDER           = 0   # 按注册顺序轮询
    PRIORITY        = 1   # 按优先级
    ORDER_BALANCE   = 2   # 公平顺序
    PRIORITY_BALANCE = 3  # 公平优先级


# ── host_bus_t ───────────────────────────────────────────────
@dataclass
class HostBus:
    """总线描述符 (对应 host_bus_t)"""
    bus_id: int                     # 总线 ID
    bus_type: ComType               # 总线类型
    speed: int = 5_000_000          # 总线速度 (Hz)
    param: int = 0                  # 总线参数 (SPI line数等)

    # 内部状态
    _ops: Optional[ComOps] = None
    _state: str = "NULL"            # NULL → INIT → OPEN → BUSY
    _sem_right: int = 0             # 使用权信号量 ID
    _devices: dict[int, "HostBusDevice"] = field(default_factory=dict)  # virtual_id → device
    _dev_count: int = 0
    _dev_open_count: int = 0
    _occupy_id: int = 0             # 当前占用总线的设备 virtual_id
    _allow_use: bool = True

    def init(self, ops: ComOps) -> int:
        """初始化总线"""
        self._ops = ops
        result = ops.init(self.bus_id, self.speed, self.param)
        if result == 0:
            self._state = "INIT"
            self._sem_right = sem_create(1, 1)  # 二值信号量, 初始空闲
            logger.info(f"Bus[{self.bus_type.name}][{self.bus_id}] initialized, speed={self.speed}")
        return result

    def deinit(self) -> int:
        if self._ops:
            self._ops.deinit(self.bus_id)
        sem_delete(self._sem_right)
        self._state = "NULL"
        return 0

    def take_right(self, virtual_id: int, timeout_ms: int = 100) -> bool:
        """获取总线使用权

        Args:
            virtual_id: 请求总线的设备 virtual_id
            timeout_ms: 超时
        Returns:
            True=获取成功
        """
        if sem_take(self._sem_right, timeout_ms) == 0:
            self._occupy_id = virtual_id
            self._state = "BUSY"
            return True
        return False

    def release_right(self, virtual_id: int):
        """释放总线使用权"""
        if self._occupy_id == virtual_id:
            self._occupy_id = 0
            self._state = "OPEN" if self._dev_open_count > 0 else "INIT"
            sem_give(self._sem_right)

    def attach_device(self, device: "HostBusDevice") -> int:
        """挂载设备到总线"""
        if device.virtual_id in self._devices:
            return -1
        self._devices[device.virtual_id] = device
        self._dev_count += 1
        device._bus = self
        return 0

    def detach_device(self, device: "HostBusDevice") -> int:
        if device.virtual_id in self._devices:
            del self._devices[device.virtual_id]
            self._dev_count -= 1
            device._bus = None
        return 0

    def device_open(self, virtual_id: int) -> int:
        """标记设备开启"""
        self._dev_open_count += 1
        if self._state == "INIT":
            self._state = "OPEN"
        return 0

    def device_close(self, virtual_id: int) -> int:
        self._dev_open_count = max(0, self._dev_open_count - 1)
        if self._dev_open_count == 0:
            self._state = "INIT"
        return 0

    def write(self, virtual_id: int, data: bytes) -> int:
        """写数据 (占用总线后调用)"""
        if self._ops is None:
            return -1
        return self._ops.write(self.bus_id, data)

    def read(self, virtual_id: int, size: int) -> bytes:
        """读数据 (占用总线后调用)"""
        if self._ops is None:
            return b""
        return self._ops.read(self.bus_id, size)


# ── 全局总线树 (对应 static host_slist_t host_bus_tree[HOST_BUS_TYPE_NUM]) ──

class BusTree:
    """全局总线树, 管理所有总线"""
    _buses: dict[tuple[int, int], HostBus] = {}  # (bus_type, bus_id) → HostBus

    @classmethod
    def register(cls, bus_type: ComType, bus_id: int,
                 speed: int, param: int) -> Optional[HostBus]:
        """注册或查找总线

        如果 (type, id) 已存在则返回已有总线, 否则创建
        """
        key = (bus_type, bus_id)
        if key in cls._buses:
            return cls._buses[key]

        bus = HostBus(bus_id=bus_id, bus_type=bus_type, speed=speed, param=param)
        ops = get_com_ops_by_type(bus_type)
        result = bus.init(ops)
        if result == 0:
            cls._buses[key] = bus
            return bus
        return None

    @classmethod
    def find(cls, bus_type: ComType, bus_id: int) -> Optional[HostBus]:
        return cls._buses.get((bus_type, bus_id))

    @classmethod
    def deinit_all(cls):
        for bus in cls._buses.values():
            bus.deinit()
        cls._buses.clear()
