"""
HIF SPI 传输层 (基于 Linux spidev)

STM32MP157 Linux 上通过 /dev/spidevX.Y 操作 SPI
- Device 为 SPI Slave (SPI0), Host 为 Master
- Host→Device: CS拉低 → 发送数据 → CS拉高
- Device→Host: Host 发 Dummy 字节轮询 (POLL 模式)
- Device 用 Notify IO (PA6, GPIO IRQ) 通知 Host 有数据
"""

import os
import struct
import time
import logging

from .transport import HifTransport

logger = logging.getLogger(__name__)


# ── Linux SPI ioctl 常量 ────────────────────────────────────────────
try:
    import fcntl
    # SPI ioctl 命令
    SPI_IOC_WR_MODE         = 0x40016B01
    SPI_IOC_RD_MODE         = 0x80016B01
    SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
    SPI_IOC_RD_BITS_PER_WORD = 0x80016B03
    SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
    SPI_IOC_RD_MAX_SPEED_HZ = 0x80046B04

    # SPI 模式
    SPI_MODE_0 = 0  # CPOL=0, CPHA=0
    SPI_MODE_1 = 1
    SPI_MODE_2 = 2
    SPI_MODE_3 = 3
except ImportError:
    logger.warning("fcntl not available, SPI ioctl will not work (non-Linux)")
    SPI_IOC_WR_MODE = SPI_IOC_RD_MODE = 0
    SPI_IOC_WR_BITS_PER_WORD = SPI_IOC_RD_BITS_PER_WORD = 0
    SPI_IOC_WR_MAX_SPEED_HZ = SPI_IOC_RD_MAX_SPEED_HZ = 0
    SPI_MODE_0 = 0


# ── SPI 传输报文结构 (用于 ioctl) ────────────────────────────────────
# struct spi_ioc_transfer {
#     __u64 tx_buf; __u64 rx_buf;
#     __u32 len; __u32 speed_hz;
#     __u16 delay_usecs; __u8 bits_per_word;
#     __u8 cs_change; __u8 tx_nbits; __u8 rx_nbits;
#     __u8 word_delay_usecs; __u8 pad;
#     /* extended fields for dual/quad */
# };
SPI_IOC_MESSAGE_1 = 0x40206B00  # SPI_IOC_MESSAGE(1)


class SpiTransport(HifTransport):
    """Linux SPI 传输层

    用法:
        transport = SpiTransport("/dev/spidev0.0", speed_hz=5000000)
        transport.open()
        transport.write(data)
        response = transport.read(256)
        transport.close()
    """

    def __init__(self, device: str = "/dev/spidev0.0",
                 speed_hz: int = 5_000_000,
                 mode: int = 0,
                 bits_per_word: int = 8,
                 notify_gpio: int | None = None):
        """
        Args:
            device: SPI 设备节点, 如 "/dev/spidev0.0"
            speed_hz: SPI 时钟频率, 默认 5MHz
            mode: SPI 模式 (0~3)
            bits_per_word: 每字位数, 默认 8
            notify_gpio: Notify IO GPIO 编号 (用于 IRQ 等待), PA6 对应 gpiochip 编号
        """
        self.device = device
        self.speed_hz = speed_hz
        self.mode = mode
        self.bits_per_word = bits_per_word
        self.notify_gpio = notify_gpio
        self._fd: int | None = None

    # ── Transport Interface ────────────────────────────────────

    def open(self) -> bool:
        """打开 SPI 设备并配置"""
        try:
            self._fd = os.open(self.device, os.O_RDWR)
            logger.info(f"SPI opened: {self.device} fd={self._fd}")

            # 设置模式
            fcntl.ioctl(self._fd, SPI_IOC_WR_MODE,
                        struct.pack("B", self.mode))

            # 设置字长
            fcntl.ioctl(self._fd, SPI_IOC_WR_BITS_PER_WORD,
                        struct.pack("B", self.bits_per_word))

            # 设置速度
            fcntl.ioctl(self._fd, SPI_IOC_WR_MAX_SPEED_HZ,
                        struct.pack("<I", self.speed_hz))

            logger.info(f"SPI configured: mode={self.mode}, "
                        f"bits={self.bits_per_word}, speed={self.speed_hz}Hz")
            return True

        except OSError as e:
            logger.error(f"Failed to open SPI {self.device}: {e}")
            self._fd = None
            return False

    def close(self):
        """关闭 SPI 设备"""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            logger.info("SPI closed")

    def write(self, data: bytes) -> int:
        """SPI 写入数据

        Host 发数据给 Device: CS拉低 → MOSI发送 → CS拉高
        """
        if self._fd is None:
            logger.error("SPI not open")
            return -1

        try:
            # 使用 os.write (half-duplex write)
            written = os.write(self._fd, data)
            logger.debug(f"SPI write: {len(data)} bytes, written={written}")
            return written
        except OSError as e:
            logger.error(f"SPI write error: {e}")
            return -1

    def read(self, size: int, timeout_ms: int = 100) -> bytes:
        """SPI 读取数据

        POLL 模式: Host 发送 dummy 字节来读取 Device 的 MISO 数据
        (SPI 全双工特性: 发 dummy 的同时可以收数据)
        """
        if self._fd is None:
            logger.error("SPI not open")
            return b""

        try:
            result = os.read(self._fd, size)
            logger.debug(f"SPI read: requested={size}, got={len(result)}")
            return result
        except OSError as e:
            logger.error(f"SPI read error: {e}")
            return b""

    def transfer(self, tx_data: bytes) -> bytes:
        """SPI 全双工传输 (同时收发)

        发送 tx_data, 同时接收等长的数据
        """
        if self._fd is None:
            logger.error("SPI not open")
            return b""

        try:
            # 使用 spidev 的全双工模式: write 数据, read 返回 MISO 数据
            # 在 Linux spidev 中, write+read 默认是全双工
            result = os.read(self._fd, len(tx_data))
            logger.debug(f"SPI transfer: tx={len(tx_data)}, rx={len(result)}")
            return result
        except OSError as e:
            logger.error(f"SPI transfer error: {e}")
            return b""

    def is_open(self) -> bool:
        return self._fd is not None

    # ── GPIO Notify ────────────────────────────────────────────

    def wait_notify(self, timeout_ms: int = 500) -> bool:
        """等待 Device 的 Notify IO 引脚拉低/拉高

        Device 通过 PA6 通知 Host 有数据就绪。
        在 SPI/I2C 模式下, Host 用 POLL 轮询 Notify 引脚。

        返回: True=有数据就绪, False=超时
        """
        if self.notify_gpio is None:
            # 没有配置 GPIO, 用延时模拟
            time.sleep(0.001)
            return True

        # GPIO 读取 (需要 /sys/class/gpio)
        gpio_path = f"/sys/class/gpio/gpio{self.notify_gpio}/value"
        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < timeout_ms:
            try:
                with open(gpio_path, "r") as f:
                    val = f.read().strip()
                if val == "1":
                    return True
            except OSError:
                pass
            time.sleep(0.0005)  # 500us 轮询

        return False
