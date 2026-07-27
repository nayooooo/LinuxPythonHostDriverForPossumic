"""
HAL IO 抽象层 (对应 hal_io.h + port_io.c)

GPIO 引脚管理, STM32MP157 Linux 通过 /sys/class/gpio 操作
"""

import os
import logging

from .types import IoMode, IoPull, IoValue, IoIrqTrig

logger = logging.getLogger("hal.io")

# GPIO base path
GPIO_BASE = "/sys/class/gpio"


def _export(pin: int):
    """导出 GPIO"""
    try:
        path = f"{GPIO_BASE}/gpio{pin}"
        if not os.path.exists(path):
            with open(f"{GPIO_BASE}/export", "w") as f:
                f.write(str(pin))
    except OSError as e:
        logger.warning(f"GPIO export pin{pin}: {e}")


def _unexport(pin: int):
    """取消导出"""
    try:
        with open(f"{GPIO_BASE}/unexport", "w") as f:
            f.write(str(pin))
    except OSError:
        pass


def mode_set(pin: int, mode: IoMode, pull: IoPull = IoPull.NO) -> int:
    """设置引脚模式"""
    _export(pin)
    gpio_dir = f"{GPIO_BASE}/gpio{pin}/direction"
    try:
        if mode == IoMode.OUTPUT:
            with open(gpio_dir, "w") as f:
                f.write("out")
        elif mode in (IoMode.INPUT, IoMode.INTERRUPT):
            with open(gpio_dir, "w") as f:
                f.write("in")
        elif mode == IoMode.DISABLE:
            _unexport(pin)
        return 0
    except OSError as e:
        logger.error(f"GPIO mode_set pin{pin}: {e}")
        return -1


def write(pin: int, value: IoValue) -> int:
    """写引脚电平"""
    gpio_val = f"{GPIO_BASE}/gpio{pin}/value"
    try:
        with open(gpio_val, "w") as f:
            f.write("1" if value == IoValue.HIGH else "0")
        return 0
    except OSError:
        return -1


def read(pin: int) -> IoValue:
    """读引脚电平"""
    gpio_val = f"{GPIO_BASE}/gpio{pin}/value"
    try:
        with open(gpio_val, "r") as f:
            v = f.read().strip()
        return IoValue.HIGH if v == "1" else IoValue.LOW
    except OSError:
        return IoValue.LOW


def irq_cfg(pin: int, trigger: IoIrqTrig, callback=None) -> int:
    """配置中断 (Python 用轮询模拟)

    实际在 Linux 中可用 epoll + gpio edge 文件实现,
    此处提供轮询 fallback
    """
    gpio_edge = f"{GPIO_BASE}/gpio{pin}/edge"
    edge_map = {
        IoIrqTrig.RISING: "rising",
        IoIrqTrig.FALLING: "falling",
        IoIrqTrig.BOTH: "both",
    }
    edge = edge_map.get(trigger, "none")
    try:
        with open(gpio_edge, "w") as f:
            f.write(edge)
        if callback:
            # 注册回调 (简化实现: 存到字典)
            _irq_callbacks[pin] = (trigger, callback)
        return 0
    except OSError:
        return -1


_irq_callbacks: dict[int, tuple] = {}  # pin -> (trigger, callback)


def irq_enable(pin: int) -> int:
    """启用引脚中断"""
    return 0


def irq_disable(pin: int) -> int:
    """禁用引脚中断"""
    return 0
