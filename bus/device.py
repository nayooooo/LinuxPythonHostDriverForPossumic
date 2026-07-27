"""
COM Device 设备层 (对应 host_llc_com_device.h/.c)

管理挂载到总线上的具体设备, 包含:
  - 设备硬件配置 (DevHw_t)
  - GPIO 引脚 (UPD/RST/Notify)
  - 上传类型 (ACTIVE/PASSIVE) 和通知类型
  - 虚拟 ID 生成
"""

import random
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from .bus import HostBus, BusTree
from hal.types import ComType, UploadType, NotifyType

logger = logging.getLogger("bus.device")


# ── 设备参数 (对应 DevParam 联合体) ──────────────────────────
@dataclass
class DevParam:
    """设备特定参数"""
    cs_pin: int = 0        # SPI CS 引脚
    addr: int = 0          # I2C 地址
    port_id: int = 0       # UART 端口 ID


# ── DevHw_t 硬件配置 ─────────────────────────────────────────
@dataclass
class DevHw:
    """设备硬件配置 (对应 DevHw_t)"""
    bus_type: ComType = ComType.SPI
    bus_id: int = 0
    bus_speed: int = 5_000_000
    bus_param: int = 1          # SPI: 1/2/4 线

    # GPIO 引脚
    upd_io: int = 0             # 更新/UPD 引脚
    rst_io: int = 0             # 复位引脚
    notify_io: int = 6          # 通知引脚 (PA6=6)

    # 上传/通知类型
    upload_type: UploadType = UploadType.PASSIVE   # SPI 模式: Host 轮询
    notify_type: NotifyType = NotifyType.EDGE      # GPIO 边沿通知

    # 设备参数
    param: DevParam = field(default_factory=DevParam)

    def validate(self) -> bool:
        """验证 upload_type 和 notify_type 兼容性

        SPI PASSIVE: notify_type 应为 EDGE 或 COM_ISR
        UART ACTIVE: 可以用 EDGE/COM_ISR/COM_IRQ_THREAD
        """
        if self.upload_type == UploadType.PASSIVE:
            if self.notify_type not in (NotifyType.EDGE, NotifyType.COM_ISR):
                logger.warning("PASSIVE upload prefers EDGE/COM_ISR notify")
        return True


# ── host_bus_device_t ─────────────────────────────────────────
@dataclass
class HostBusDevice:
    """总线设备描述符 (对应 host_bus_device_t)"""

    # 硬件配置
    hw: DevHw

    # 运行时状态
    virtual_id: int = 0             # 全网唯一虚拟 ID
    _bus: Optional[HostBus] = None  # 所属总线 (挂载后填充)
    working: bool = False           # 0=close, 1=open

    @property
    def bus_type(self) -> ComType:
        return self.hw.bus_type

    @property
    def bus_id(self) -> int:
        return self.hw.bus_id

    @property
    def bus(self) -> Optional[HostBus]:
        return self._bus


# ── 设备注册 ──────────────────────────────────────────────────
# 全局虚拟 ID 注册表 (防止冲突)
_virtual_id_registry: set[int] = set()


def _generate_virtual_id() -> int:
    """生成唯一虚拟 ID (仿照 C 代码的随机+线性扫描)"""
    for _ in range(100):
        vid = random.randint(0x1000, 0xFFFF)
        if vid not in _virtual_id_registry:
            return vid

    # 线性扫描
    for vid in range(0x1000, 0xFFFF):
        if vid not in _virtual_id_registry:
            return vid

    raise RuntimeError("No available virtual_id")


def device_register(hw: DevHw) -> Optional[HostBusDevice]:
    """注册设备到总线

    Returns:
        HostBusDevice 或 None
    """
    hw.validate()

    # 1. 注册/查找总线
    bus = BusTree.register(hw.bus_type, hw.bus_id, hw.bus_speed, hw.bus_param)
    if bus is None:
        logger.error(f"Failed to register bus [{hw.bus_type.name}][{hw.bus_id}]")
        return None

    # 2. 生成虚拟 ID
    vid = _generate_virtual_id()
    _virtual_id_registry.add(vid)

    # 3. 创建设备
    device = HostBusDevice(hw=hw, virtual_id=vid)

    # 4. 挂载到总线
    bus.attach_device(device)

    logger.info(f"Device registered: vid=0x{vid:04X}, "
                f"bus={hw.bus_type.name}[{hw.bus_id}], "
                f"upload={hw.upload_type.name}")
    return device


def device_unregister(device: HostBusDevice) -> int:
    """注销设备"""
    if device.virtual_id in _virtual_id_registry:
        _virtual_id_registry.discard(device.virtual_id)

    if device._bus:
        device._bus.detach_device(device)

    logger.info(f"Device unregistered: vid=0x{device.virtual_id:04X}")
    return 0
