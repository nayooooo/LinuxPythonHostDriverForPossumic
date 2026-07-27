"""
HIF Transport - 传输层抽象接口

具体传输层 (SPI/UART/I2C) 需实现此接口
"""

from abc import ABC, abstractmethod


class HifTransport(ABC):
    """HIF 传输层抽象基类"""

    @abstractmethod
    def open(self) -> bool:
        """打开传输通道"""

    @abstractmethod
    def close(self):
        """关闭传输通道"""

    @abstractmethod
    def write(self, data: bytes) -> int:
        """发送数据, 返回发送字节数"""

    @abstractmethod
    def read(self, size: int, timeout_ms: int = 100) -> bytes:
        """读取数据, 返回读取的字节 (可能少于请求的字节数)"""

    @abstractmethod
    def is_open(self) -> bool:
        """检查传输通道是否打开"""
