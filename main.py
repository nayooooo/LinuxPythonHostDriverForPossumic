#!/usr/bin/env python3
"""
Possumic RS6x/7x 毫米波雷达 HIF Host 驱动 (简化版)
====================================================
STM32MP157 Linux, SPI 模式

功能:
  - NOTIFY IO 中断驱动接收 (gpiolib, Mode.INTERRUPT + Edge.RISING)
  - SPI 数据接收 (spidev, POLL 模式)
  - HIF Frame 解码 (Magic/Check8/MsgHeader/Payload/Check32)
  - 分片数据自动重组 (基于 frag/more 标志)
  - 回调式 API: 注册一个回调，完整帧自动回调

HIF Frame 结构:
  ┌────────┬────────┬────────────────────┬───────────┬──────────┐
  │Magic   │Check8  │ MsgHeader (4B)     │ Payload   │ Check32  │
  │0xA5(1B)│1B      │[Ctrl|MsgID|Len+Seq]│ 0~4095 B  │ 4B       │
  └────────┴────────┴────────────────────┴───────────┴──────────┘
  Check8  = ~sum8(Magic + MsgHeader)
  Check32 = ~sum32(MsgHeader + Payload) DWORD aligned

MsgHeader (4B, LSB):
  Byte0: [Type(2)|Req(1)|Enc(1)|HasChk(1)|More(1)|Ext(1)|Mac(1)]
  Byte1: MsgID
  Byte2-3: [Length(12)|Seq(3)|Frag(1)]
    - Length: 载荷长度 0~4095
    - Seq:    流序列号 0~7
    - Frag:   0=末片/完整帧, 1=还有更多分片

用法:
    from main import RadarReceiver

    def on_frame(msg_id: int, payload: bytes):
        print(f"Frame: MsgID=0x{msg_id:02X}, size={len(payload)}")

    rx = RadarReceiver("/dev/spidev0.0", speed_hz=5_000_000,
                       notify_chip="/dev/gpiochip0", notify_line=6)
    rx.on_frame = on_frame
    rx.start()
    # ... 数据自动到达, NOTIFY IO 触发后完整帧自动回调 ...
    rx.stop()
"""

import os
import struct
import time
import threading
import logging
from typing import Callable, Optional, Dict
from dataclasses import dataclass

# ── gpiolib 导入 (Linux 开发板已预装) ──
try:
    from gpiolib import GPIO as GpioLibGPIO, Mode as GpioMode, Edge as GpioEdge
    _HAS_GPIOLIB = True
except ImportError:
    _HAS_GPIOLIB = False
    GpioLibGPIO = None  # type: ignore
    GpioMode = None      # type: ignore
    GpioEdge = None      # type: ignore


# ====================================================================
#  HIF 协议常量
# ====================================================================

HIF_MAGIC          = 0xA5
HIF_HEADER_SIZE    = 6      # Magic(1) + Check8(1) + MsgHeader(4)
HIF_CHECKSUM_SIZE  = 4      # Check32
HIF_MAX_PAYLOAD    = 4095
HIF_FRAME_MAX      = HIF_HEADER_SIZE + HIF_MAX_PAYLOAD + HIF_CHECKSUM_SIZE

# Report MsgID
MSGID_C1_FFT       = 0xC1   # 1D Range FFT
MSGID_C2_FFT       = 0xC2   # 2D Range-Doppler FFT
MSGID_C3_POINTS    = 0xC3   # 目标检测点云
MSGID_C6_PSIC      = 0xC6   # PSIC Debug 数据

REPORT_MSG_IDS = {MSGID_C1_FFT, MSGID_C2_FFT, MSGID_C3_POINTS, MSGID_C6_PSIC}


# ====================================================================
#  Checksum 计算
# ====================================================================

def check8(magic: int, msghdr: bytes) -> int:
    """Check8 = ~sum8(Magic + MsgHeader)"""
    return (~(magic + sum(msghdr))) & 0xFF


def check32(msghdr: bytes, payload: bytes) -> int:
    """Check32 = ~sum32(MsgHeader + Payload), DWORD 对齐

    数据按 4 字节一组累加 (little-endian uint32),
    末尾不足 4 字节时补 0x00 对齐后累加。
    """
    data = msghdr + payload
    total = 0
    for i in range(0, len(data) - 3, 4):
        total = (total + struct.unpack_from("<I", data, i)[0]) & 0xFFFFFFFF
    rem = len(data) % 4
    if rem:
        last = data[-rem:] + b"\x00" * (4 - rem)
        total = (total + struct.unpack("<I", last)[0]) & 0xFFFFFFFF
    return (~total) & 0xFFFFFFFF


# ====================================================================
#  MsgHeader 解析
# ====================================================================

@dataclass
class MsgHeader:
    """HIF 消息头解析结果"""
    type_: int          # 2 bits: 0=H2H, 1=H2D, 2=D2H
    req: int            # 1 bit:  0=Response, 1=Command
    enc: int            # 1 bit:  加密标志
    has_checksum: int   # 1 bit:  是否含 Check32
    more: int           # 1 bit:  更多分片标志 (byte0 bit5)
    ext: int            # 1 bit:  扩展标志
    mac: int            # 1 bit:  MAC 标志
    msg_id: int         # 8 bits: 消息 ID
    length: int         # 12 bits: 载荷长度 (0~4095)
    seq: int            # 3 bits:  流序列号 (0~7)
    frag: int           # 1 bit:  分片标志 (0=末片, 1=更多)

    @property
    def is_fragment(self) -> bool:
        """是否为分片 (需要重组)"""
        return self.frag == 1 or self.more == 1

    @property
    def is_last(self) -> bool:
        """是否为最后一个分片 (或完整帧)"""
        return self.frag == 0 and self.more == 0


def parse_msgheader(data: bytes) -> Optional[MsgHeader]:
    """解析 4 字节 MsgHeader

    Returns:
        MsgHeader 对象, 数据不足 4 字节时返回 None
    """
    if len(data) < 4:
        return None

    byte0, byte1, len_seq_frag = struct.unpack("<BBH", data[:4])

    return MsgHeader(
        type_         = byte0 & 0x03,
        req           = (byte0 >> 2) & 0x01,
        enc           = (byte0 >> 3) & 0x01,
        has_checksum  = (byte0 >> 4) & 0x01,
        more          = (byte0 >> 5) & 0x01,
        ext           = (byte0 >> 6) & 0x01,
        mac           = (byte0 >> 7) & 0x01,
        msg_id        = byte1,
        length        = len_seq_frag & 0x0FFF,
        seq           = (len_seq_frag >> 12) & 0x07,
        frag          = (len_seq_frag >> 15) & 0x01,
    )


# ====================================================================
#  HIF Frame 解包
# ====================================================================

@dataclass
class HifFrame:
    """解析后的 HIF 帧"""
    header: MsgHeader
    payload: bytes
    check8_ok: bool
    valid: bool         # Check32 校验通过

    @property
    def msg_id(self) -> int:
        return self.header.msg_id

    @property
    def payload_len(self) -> int:
        return len(self.payload)


def unpack_frame(data: bytes) -> Optional[HifFrame]:
    """解包 HIF Frame

    Args:
        data: 完整帧数据 (至少 Header + Payload + Check32)

    Returns:
        HifFrame 或 None (magic 不匹配/数据不完整)
    """
    # 最小帧: Magic(1) + Check8(1) + MsgHeader(4) + Check32(4) = 10 bytes
    if len(data) < HIF_HEADER_SIZE + HIF_CHECKSUM_SIZE:
        return None

    # 验证 Magic
    if data[0] != HIF_MAGIC:
        return None

    check8_recv = data[1]
    msghdr_raw  = data[2:6]

    # 解析 Header
    hdr = parse_msgheader(msghdr_raw)
    if hdr is None:
        return None

    # 验证长度
    payload_len = hdr.length
    if payload_len > HIF_MAX_PAYLOAD:
        return None

    total = HIF_HEADER_SIZE + payload_len + HIF_CHECKSUM_SIZE
    if len(data) < total:
        return None

    # 提取 Payload 和 Check32
    payload      = data[6 : 6 + payload_len]
    checksum_off = 6 + payload_len
    checksum_recv = struct.unpack("<I", data[checksum_off : checksum_off + 4])[0]

    # 验证 Check8
    c8_ok = (check8_recv == check8(HIF_MAGIC, msghdr_raw))

    # 验证 Check32
    c32_match = (checksum_recv == check32(msghdr_raw, payload))

    return HifFrame(
        header    = hdr,
        payload   = payload,
        check8_ok = c8_ok,
        valid     = c32_match,
    )


# ====================================================================
#  分片重组器
# ====================================================================

class FragmentAssembler:
    """分片重组器

    按 msg_id 分组累积载荷分片, 收到末片后组装完整数据。
    支持超时自动清理。
    """

    def __init__(self, msg_id: int, seq: int = 0):
        self.msg_id     = msg_id
        self.seq        = seq
        self.parts: list[bytes] = []
        self.total_len  = 0
        self.frag_count = 0
        self.start_time = time.monotonic()

    def add(self, payload: bytes):
        """添加一个分片"""
        self.parts.append(payload)
        self.total_len += len(payload)
        self.frag_count += 1

    def assemble(self) -> bytes:
        """组装完整数据"""
        if len(self.parts) == 1:
            return self.parts[0]
        return b"".join(self.parts)

    @property
    def age(self) -> float:
        """从创建到现在的秒数"""
        return time.monotonic() - self.start_time


# ====================================================================
#  SPI 设备操作
# ====================================================================

class SpiDev:
    """Linux SPI 设备操作 (基于 spidev)

    使用 /dev/spidevX.Y 和 ioctl 配置 SPI 参数。
    POLL 模式: Host 读 MISO 数据 (发送 dummy 字节由内核处理)。
    """

    # ioctl 命令 (linux/spi/spidev.h)
    _IOC_WR_MODE         = 0x40016B01
    _IOC_WR_BITS         = 0x40016B03
    _IOC_WR_SPEED        = 0x40046B04

    def __init__(self, device: str = "/dev/spidev0.0",
                 speed_hz: int = 5_000_000,
                 mode: int = 0,
                 bits_per_word: int = 8):
        """
        Args:
            device:        SPI 设备节点, 如 "/dev/spidev0.0"
            speed_hz:      SPI 时钟频率, 默认 5 MHz
            mode:          SPI 模式 (0=CPOL0/CPHA0 ~ 3)
            bits_per_word: 每字位数, 默认 8
        """
        self.device        = device
        self.speed_hz      = speed_hz
        self.mode          = mode
        self.bits_per_word = bits_per_word
        self._fd: Optional[int] = None

    def open(self) -> bool:
        """打开 SPI 设备并配置"""
        try:
            import fcntl
            self._fd = os.open(self.device, os.O_RDWR)

            fcntl.ioctl(self._fd, self._IOC_WR_MODE,
                        struct.pack("B", self.mode))
            fcntl.ioctl(self._fd, self._IOC_WR_BITS,
                        struct.pack("B", self.bits_per_word))
            fcntl.ioctl(self._fd, self._IOC_WR_SPEED,
                        struct.pack("<I", self.speed_hz))
            return True
        except OSError as e:
            logging.error(f"SPI open failed [{self.device}]: {e}")
            self._fd = None
            return False

    def close(self):
        """关闭 SPI 设备"""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def read(self, size: int) -> bytes:
        """读取 SPI 数据 (POLL 模式: dummy write + MISO read)"""
        if self._fd is None:
            return b""
        try:
            return os.read(self._fd, size)
        except OSError:
            return b""

    def write(self, data: bytes) -> int:
        """写入 SPI 数据"""
        if self._fd is None:
            return -1
        try:
            return os.write(self._fd, data)
        except OSError:
            return -1

    @property
    def is_open(self) -> bool:
        return self._fd is not None


# ====================================================================
#  雷达数据接收器 (核心)
# ====================================================================

FrameCallback = Callable[[int, bytes], None]
"""帧回调类型: callback(msg_id, payload)

- msg_id:  消息 ID (0xC1/C2/C3/C6 等)
- payload: 完整帧载荷 (分片已自动重组)
"""


class RadarReceiver:
    """雷达数据接收器

    特性:
      - NOTIFY IO 中断驱动: Device PA6 上升沿 → gpiolib.check() → 读取 SPI
      - 自动 HIF Frame 解析 (Magic 对齐 / Check8 / Check32)
      - 自动分片重组 (基于 frag/more 标志)
      - 单回调接口: on_frame(msg_id, payload)
      - 后台线程异步接收
      - 超时自动清理未完成分片

    用法:
        rx = RadarReceiver(
            "/dev/spidev0.0", speed_hz=5_000_000,
            notify_chip="/dev/gpiochip0", notify_line=6,
        )
        rx.on_frame = lambda mid, data: print(f"Frame 0x{mid:02X}: {len(data)}B")
        rx.start()
        # ... NOTIFY IO 触发后数据自动到达 ...
        rx.stop()
    """

    def __init__(self,
                 spi_device: str = "/dev/spidev0.0",
                 speed_hz: int = 5_000_000,
                 spi_mode: int = 0,
                 notify_chip: Optional[str] = None,
                 notify_line: Optional[int] = None,
                 notify_edge: str = "rising",
                 read_size: int = 4096,
                 frag_timeout: float = 5.0,
                 log_level: int = logging.INFO):
        """
        Args:
            spi_device:   SPI 设备节点路径, 如 "/dev/spidev0.0"
            speed_hz:     SPI 时钟频率 (Hz), 默认 5 MHz
            spi_mode:     SPI 模式 (0~3), 默认 0
            notify_chip:  NOTIFY IO GPIO 芯片路径, 如 "/dev/gpiochip0"
                          None 则降级为纯 SPI 轮询模式
            notify_line:  NOTIFY IO GPIO 引脚编号, PA6 对应 6
            notify_edge:  边沿触发类型: "rising" / "falling" / "both"
            read_size:    每次 SPI 读取字节数, 默认 4096
            frag_timeout: 分片重组超时 (秒), 超时后丢弃不完整数据
            log_level:    日志级别
        """
        self.spi = SpiDev(spi_device, speed_hz, spi_mode)
        self.read_size    = read_size
        self.frag_timeout = frag_timeout

        # NOTIFY IO 配置
        self._notify_chip  = notify_chip
        self._notify_line  = notify_line
        self._notify_edge  = notify_edge
        self._notify_gpio: any = None  # gpiolib.GPIO 实例

        # 用户回调
        self._callback: Optional[FrameCallback] = None

        # 分片重组状态
        self._assemblers: Dict[int, FragmentAssembler] = {}
        self._lock = threading.Lock()

        # 接收线程
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 统计
        self.frame_count = 0
        self.frag_count  = 0
        self.error_count = 0
        self.byte_count  = 0

        # 日志
        self._log = logging.getLogger("RadarRX")
        if not self._log.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter(
                "[%(levelname)s] %(name)s: %(message)s"))
            self._log.addHandler(h)
            self._log.setLevel(log_level)

    # ── 回调接口 ───────────────────────────────────────────

    @property
    def on_frame(self) -> Optional[FrameCallback]:
        """获取当前帧回调"""
        return self._callback

    @on_frame.setter
    def on_frame(self, cb: FrameCallback):
        """设置帧回调

        每收到一个完整帧 (分片已自动重组) 调用一次:
            cb(msg_id: int, payload: bytes)
        """
        self._callback = cb

    # ── 启停控制 ───────────────────────────────────────────

    def start(self) -> bool:
        """启动接收

        1. 打开 SPI 设备 (spidev)
        2. 打开 NOTIFY IO (gpiolib, INTERRUPT 模式 + 边沿触发)
        3. 启动后台接收线程

        返回 True 表示成功, False 表示 SPI 打开失败。
        """
        if self._running:
            self._log.warning("Already running")
            return True

        # 打开 SPI
        if not self.spi.open():
            return False

        # 打开 NOTIFY IO (gpiolib INTERRUPT 模式)
        if (self._notify_chip is not None and
                self._notify_line is not None):
            if not _HAS_GPIOLIB:
                self._log.warning(
                    "gpiolib not installed, NOTIFY IO disabled. "
                    "Falling back to SPI polling mode."
                )
            else:
                try:
                    edge_map = {
                        "rising":  GpioEdge.RISING,
                        "falling": GpioEdge.FALLING,
                        "both":    GpioEdge.BOTH,
                    }
                    edge = edge_map.get(
                        self._notify_edge, GpioEdge.RISING)

                    self._notify_gpio = GpioLibGPIO(
                        chip=self._notify_chip,
                        line=self._notify_line,
                        mode=GpioMode.INTERRUPT,
                        edge=edge,
                        bias="pull_down",
                        label="radar_notify",
                    )
                    self._notify_gpio.open()
                    self._log.info(
                        f"NOTIFY IO opened: {self._notify_chip} "
                        f"line {self._notify_line}, "
                        f"edge={self._notify_edge}"
                    )
                except Exception as e:
                    self._log.warning(
                        f"NOTIFY IO open failed: {e}. "
                        f"Falling back to SPI polling mode."
                    )
                    self._notify_gpio = None

        self._running = True
        self._thread = threading.Thread(
            target=self._recv_loop,
            name="RadarRX",
            daemon=True,
        )
        self._thread.start()

        mode_str = "interrupt" if self._notify_gpio else "polling"
        self._log.info(
            f"Receiver started on {self.spi.device} "
            f"@ {self.spi.speed_hz / 1e6:.1f} MHz ({mode_str})"
        )
        return True

    def stop(self):
        """停止接收

        关闭接收线程、NOTIFY IO、SPI 设备, 清理统计。
        """
        self._running = False

        # 等待线程退出
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None

        # 关闭 NOTIFY IO
        if self._notify_gpio is not None:
            try:
                self._notify_gpio.close()
                self._log.info("NOTIFY IO closed")
            except Exception as e:
                self._log.warning(f"NOTIFY IO close error: {e}")
            self._notify_gpio = None

        # 关闭 SPI
        self.spi.close()

        # 清理分片
        with self._lock:
            dropped = len(self._assemblers)
            self._assemblers.clear()

        self._log.info(
            f"Receiver stopped: "
            f"frames={self.frame_count}, "
            f"frags={self.frag_count}, "
            f"errors={self.error_count}, "
            f"bytes={self.byte_count}"
            + (f", dropped_frags={dropped}" if dropped else "")
        )

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        """获取统计信息"""
        return {
            "frames": self.frame_count,
            "fragments": self.frag_count,
            "errors": self.error_count,
            "bytes": self.byte_count,
            "pending_frags": len(self._assemblers),
        }

    # ── 接收主循环 ─────────────────────────────────────────

    def _recv_loop(self):
        """后台接收线程主循环

        中断模式 (gpiolib 可用):
          - gpio.check(timeout=0.1) 阻塞等待 NOTIFY IO 上升沿
          - 触发后立即读取 SPI 数据

        轮询模式 (gpiolib 不可用/未配置):
          - 持续从 SPI 读取数据 (1ms 间隔)

        两种模式共享相同的帧解析/分片重组/回调投递流水线。
        """
        buf = b""
        last_cleanup = time.monotonic()

        while self._running:
            try:
                # ── 等待数据就绪 ──
                if self._notify_gpio is not None:
                    # 中断模式: 阻塞等待 NOTIFY IO 上升沿
                    has_event = self._notify_gpio.check(timeout=0.1)
                    if not has_event:
                        # 无事件, 继续等待
                        continue
                    self._log.debug("NOTIFY triggered, reading SPI...")
                else:
                    # 轮询模式: 短暂休眠
                    time.sleep(0.001)

                # ── 读取 SPI 数据 ──
                chunk = self.spi.read(self.read_size)
                if chunk:
                    self.byte_count += len(chunk)
                    buf += chunk

                # ── 处理缓冲区中的完整帧 ──
                buf = self._drain_frames(buf)

                # ── 定期清理超时分片 ──
                now = time.monotonic()
                if now - last_cleanup > 1.0:
                    self._cleanup_stale()
                    last_cleanup = now

            except Exception as e:
                self._log.error(f"Recv error: {e}", exc_info=True)
                self.error_count += 1
                time.sleep(0.01)

    # ── 帧提取 ─────────────────────────────────────────────

    def _drain_frames(self, buf: bytes) -> bytes:
        """从字节缓冲区中提取所有完整 HIF Frame

        Args:
            buf: 累积的字节缓冲区

        Returns:
            未处理的剩余字节
        """
        while len(buf) >= HIF_HEADER_SIZE + HIF_CHECKSUM_SIZE:

            # 定位 Magic 0xA5
            pos = buf.find(b"\xa5")
            if pos < 0:
                return b""     # 无 Magic, 全部丢弃

            if pos > 0:
                skipped = pos
                buf = buf[pos:]
                self._log.debug(f"Skipped {skipped} bytes to find Magic")

            if len(buf) < HIF_HEADER_SIZE:
                return buf      # 等待更多数据

            # 预解析 Header 获取 Payload 长度
            hdr = parse_msgheader(buf[2:6])
            if hdr is None or hdr.length > HIF_MAX_PAYLOAD:
                buf = buf[1:]   # Header 无效, 跳过一字节重试
                continue

            total = HIF_HEADER_SIZE + hdr.length + HIF_CHECKSUM_SIZE
            if len(buf) < total:
                return buf      # 等待更多数据

            # 解包完整帧
            frame = unpack_frame(buf[:total])
            buf = buf[total:]

            if frame and frame.valid:
                self._handle_frame(frame)
            elif frame:
                # Check32 不匹配
                self._log.debug(f"Check32 fail: MsgID=0x{frame.msg_id:02X}")
                self.error_count += 1
            else:
                self.error_count += 1

        return buf

    # ── 帧处理 ─────────────────────────────────────────────

    def _handle_frame(self, frame: HifFrame):
        """处理一个有效的 HIF Frame

        分片帧 → 累积到 FragmentAssembler
        完整帧 → 直接投递回调
        """
        hdr = frame.header

        if hdr.is_fragment:
            # 分片: 累积等待重组
            self.frag_count += 1
            self._accumulate_fragment(hdr.msg_id, frame.payload, hdr)
        else:
            # 完整帧: 直接投递
            self.frame_count += 1
            self._deliver(hdr.msg_id, frame.payload)

    # ── 分片累积与重组 ─────────────────────────────────────

    def _accumulate_fragment(self, msg_id: int, payload: bytes,
                              hdr: MsgHeader):
        """累积分片数据

        策略:
          - 同一 msg_id 的新分片: 加入现有 Assembler
          - 旧 Assembler 超时被新分片覆盖时: 丢弃旧的, 创建新的
          - 收到末片 (frag=0, more=0): 组装并投递
        """
        is_last = hdr.is_last

        with self._lock:
            existing = self._assemblers.get(msg_id)

            if existing:
                # 已存在同 msg_id 的分片流
                if existing.age > self.frag_timeout:
                    # 旧流超时, 丢弃重建
                    self._log.warning(
                        f"Fragment timeout override: "
                        f"MsgID=0x{msg_id:02X}, "
                        f"old_parts={existing.frag_count}, "
                        f"old_total={existing.total_len}"
                    )
                    del self._assemblers[msg_id]
                    existing = None

            if existing is None:
                existing = FragmentAssembler(msg_id, hdr.seq)
                self._assemblers[msg_id] = existing

            existing.add(payload)

            if is_last:
                # 组装完成
                assembler = existing
                del self._assemblers[msg_id]

        # 在锁外执行组装和投递
        if is_last:
            complete = assembler.assemble()
            self.frame_count += 1
            self._log.debug(
                f"Assembly done: MsgID=0x{msg_id:02X}, "
                f"parts={assembler.frag_count}, "
                f"total={len(complete)}"
            )
            self._deliver(msg_id, complete)

    # ── 投递 ───────────────────────────────────────────────

    def _deliver(self, msg_id: int, payload: bytes):
        """投递完整帧到用户回调"""
        cb = self._callback
        if cb is not None:
            try:
                cb(msg_id, payload)
            except Exception as e:
                self._log.error(
                    f"Callback error for MsgID=0x{msg_id:02X}: {e}",
                    exc_info=True
                )

    # ── 超时清理 ───────────────────────────────────────────

    def _cleanup_stale(self):
        """清理超时未完成的分片重组"""
        now = time.monotonic()
        with self._lock:
            stale = [
                mid for mid, a in self._assemblers.items()
                if now - a.start_time > self.frag_timeout
            ]
            for mid in stale:
                a = self._assemblers.pop(mid)
                self._log.warning(
                    f"Fragment timeout: MsgID=0x{mid:02X}, "
                    f"parts={a.frag_count}, total={a.total_len}, "
                    f"age={a.age:.1f}s"
                )
                self.error_count += 1


# ====================================================================
#  示例: 独立运行
# ====================================================================

if __name__ == "__main__":
    import signal
    import sys

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s"
    )

    # ── 用户回调: 每收到一个完整帧调用一次 ──
    def on_frame(msg_id: int, payload: bytes):
        """处理一帧完整数据"""
        name = {
            MSGID_C1_FFT:    "C1 Range FFT",
            MSGID_C2_FFT:    "C2 Doppler FFT",
            MSGID_C3_POINTS: "C3 Points",
            MSGID_C6_PSIC:   "C6 PSIC Debug",
        }.get(msg_id, f"0x{msg_id:02X}")

        print(f"\n--- Frame: {name}, size={len(payload)} ---")

        # 打印前 64 字节 (hex dump)
        preview = payload[:64]
        for i in range(0, len(preview), 16):
            hex_part = " ".join(f"{b:02X}" for b in preview[i:i+16])
            ascii_part = "".join(
                chr(b) if 0x20 <= b < 0x7F else "."
                for b in preview[i:i+16]
            )
            print(f"  {i:04X}: {hex_part:<48s} {ascii_part}")
        if len(payload) > 64:
            print(f"  ... ({len(payload) - 64} more bytes)")

    # ── 命令行参数 ──
    device  = sys.argv[1] if len(sys.argv) > 1 else "/dev/spidev0.0"
    speed   = int(sys.argv[2]) if len(sys.argv) > 2 else 5_000_000
    gpio_chip = sys.argv[3] if len(sys.argv) > 3 else "/dev/gpiochip0"
    gpio_line = int(sys.argv[4]) if len(sys.argv) > 4 else 6  # PA6

    # ── 创建接收器 ──
    rx = RadarReceiver(
        spi_device=device,
        speed_hz=speed,
        notify_chip=gpio_chip,
        notify_line=gpio_line,
        log_level=logging.DEBUG,
    )
    rx.on_frame = on_frame

    if not rx.start():
        print(f"ERROR: Failed to open {device}")
        sys.exit(1)

    print(f"Receiver running on {device} @ {speed/1e6:.1f} MHz")
    print(f"NOTIFY IO: {gpio_chip} line {gpio_line}")
    print("Press Ctrl+C to stop.\n")

    # ── 信号处理 ──
    def shutdown(signum, frame):
        print("\nShutting down...")
        rx.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ── 主循环 ──
    try:
        while rx.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        rx.stop()
