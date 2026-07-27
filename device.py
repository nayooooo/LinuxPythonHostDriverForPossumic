"""
HIF Device 层 — 设备唤醒、连接、命令发送、响应接收

基于 SPI POLL 模式的 Device 交互:
1. 唤醒 (Wake): Host 发送 0x55 0xFF 0x55 0xFF, 等待 Device 回复 0x79 0x79 0x79 0x79
2. 连接 (Connect): Host 发送 MsgID=0x05, Payload=0x01
3. 命令 (Command): Host 发送 Command Frame → Device 返回 Response Frame
4. 轮询 (Poll): Host 周期发送 Poll 命令获取 Device 缓存的 Report 数据
"""

import time
import logging
import struct

from hif import (
    pack_frame, unpack_frame, parse_response_status,
    CmdMsgID, ReportMsgID, CmdStatus,
    HIF_MAX_PAYLOAD, HIF_HEADER_SIZE, HIF_CHECKSUM_SIZE, HIF_FRAME_MAX_SIZE,
    SPI_WAKE_SEQ, SPI_ACK_SEQ,
)
from transport import HifTransport

logger = logging.getLogger(__name__)


class DeviceState:
    """Device 状态机"""
    CLOSED      = "closed"
    WAKING      = "waking"
    CONNECTING  = "connecting"
    ACTIVE      = "active"
    ERROR       = "error"


class HifDevice:
    """HIF Device 驱动 (SPI POLL 模式)

    用法:
        from transport import SpiTransport

        transport = SpiTransport("/dev/spidev0.0", speed_hz=5_000_000)
        transport.open()

        device = HifDevice(transport)
        device.wake()
        device.connect()

        response = device.send_command(CmdMsgID.VERSION_GET)
        print(response)

        device.close()
    """

    def __init__(self, transport: HifTransport):
        """
        Args:
            transport: HIF 传输层实例 (SpiTransport)
        """
        self._transport = transport
        self._state = DeviceState.CLOSED
        self._seq = 0           # 当前发送序列号 (0~7)
        self._recv_buf = b""    # 接收缓冲

    # ── State ──────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    def is_active(self) -> bool:
        return self._state == DeviceState.ACTIVE

    def close(self):
        self._state = DeviceState.CLOSED
        self._transport.close()

    # ── Wake Sequence (SPI) ────────────────────────────────────

    def wake(self) -> bool:
        """SPI 唤醒序列

        流程 (手册 3.2.1.1):
        1. Host 发送: 0x55 0xFF 0x55 0xFF
        2. Host 每 1ms 读取 4 字节, 直到收到 0x79 (Device Active)
        3. Device 回复: 0x79 0x79 0x79 0x79
        4. Host 发送 ack: 0x79 0x79 0x79 0x79
        5. Device 进入 Active 状态
        """
        self._state = DeviceState.WAKING

        if not self._transport.is_open():
            logger.error("Transport not open, cannot wake")
            self._state = DeviceState.ERROR
            return False

        logger.info("Starting SPI wake sequence...")

        # Step 1: 发送 wake 序列
        logger.debug(f"  Tx wake: {SPI_WAKE_SEQ.hex()}")
        self._transport.write(SPI_WAKE_SEQ)

        # Step 2: 轮询等待 Device 回复 0x79 0x79 0x79 0x79
        # 每次发 dummy 字节读取 4 字节, 间隔 1ms
        for attempt in range(200):
            rx = self._transport.read(4, timeout_ms=5)
            if len(rx) >= 4 and rx == SPI_ACK_SEQ:
                logger.debug(f"  Rx wake ack: {rx.hex()}")
                break
            time.sleep(0.001)  # 1ms
        else:
            logger.error("Wake timeout: no ack from device")
            self._state = DeviceState.ERROR
            return False

        # Step 3: Host 发送 ack
        logger.debug(f"  Tx host ack: {SPI_ACK_SEQ.hex()}")
        self._transport.write(SPI_ACK_SEQ)

        # Step 4: 再等待一点时间确保 Device 稳定
        time.sleep(0.01)  # 10ms

        logger.info("SPI wake sequence completed, device should be active")
        return True

    # ── Connect ───────────────────────────────���────────────────

    def connect(self) -> bool:
        """设备连接

        发送 Connect 命令 (MsgID=0x05, Payload=0x01)
        Device 回复 Response 确认连接
        """
        self._state = DeviceState.CONNECTING

        logger.info("Connecting to device...")

        response = self.send_command(CmdMsgID.DEVICE_CONNECT,
                                     payload=bytes([0x01]))

        if response is None:
            logger.error("Connect failed: no response")
            self._state = DeviceState.ERROR
            return False

        if response["status"] == CmdStatus.SUCCESS:
            self._state = DeviceState.ACTIVE
            logger.info("Device connected successfully")
            return True

        logger.error(f"Connect failed: status={response['status']}")
        self._state = DeviceState.ERROR
        return False

    # ── Send Command ───────────────────────────────────────────

    def send_command(self, msg_id: int, payload: bytes = b"",
                     timeout_ms: int = 500) -> dict | None:
        """发送命令并等待响应

        Args:
            msg_id: 命令 MsgID
            payload: 命令 Payload
            timeout_ms: 响应超时 (ms)

        Returns:
            dict: {"status": int, "header": dict, "payload": bytes, "raw": bytes}
            None: 超时或错误

        流程 (手册 3.2.1.3 SPI Command-Response):
        1. Host 拉低 CS, 发送 Command Frame
        2. Host 拉高 CS
        3. Host 等待一定时间
        4. Host 拉低 CS, 发送 Dummy 字节读取 Response Frame
        5. Host 拉高 CS
        """
        # 构建命令帧
        cmd_frame = pack_frame(msg_id, payload, self._seq, is_command=True)
        self._seq = (self._seq + 1) & 0x07

        logger.debug(f"Sending Command: MsgID=0x{msg_id:02X}, "
                     f"seq={self._seq}, len={len(cmd_frame)}")

        # 发送 Command Frame
        self._transport.write(cmd_frame)

        # 短暂延时 (手册说 10ms)
        time.sleep(0.01)

        # 读取 Response Frame
        # SPI POLL: 发送 dummy 字节来读取 Response
        # 先读取 Header + Payload 的最大可能长度
        response_frame = self._read_frame(timeout_ms)

        if response_frame is None:
            logger.warning(f"Command 0x{msg_id:02X}: no response within {timeout_ms}ms")
            return None

        # 解析响应
        hdr = response_frame["header"]
        resp_payload = response_frame["payload"]

        status = CmdStatus.SUCCESS
        if len(resp_payload) >= 1:
            status = CmdStatus(resp_payload[0]) if resp_payload[0] <= 12 else resp_payload[0]

        logger.debug(f"Response: MsgID=0x{hdr['msg_id']:02X}, "
                     f"status={status.name if isinstance(status, CmdStatus) else status}, "
                     f"payload_len={len(resp_payload)}")

        return {
            "status": status,
            "header": hdr,
            "payload": resp_payload,
            "raw": response_frame,
            "valid": response_frame["valid"],
        }

    # ── Poll Report ────────────────────────────────────────────

    def poll_report(self) -> dict | None:
        """轮询获取 Device 的 Report 数据

        SPI/I2C 模式下, Host 发送 Poll 命令 (MsgID=0x0C)
        Device 如果有缓存数据就返回 Report Frame, 否则返回空 Response
        """
        response = self.send_command(CmdMsgID.POLL, timeout_ms=200)

        if response is None:
            return None

        # Poll 的 Response Payload 如果是 Report 数据,
        # 需要根据 MsgID 来判断
        hdr = response["header"]
        msg_id = hdr["msg_id"]

        if msg_id in (ReportMsgID.C1_FFT, ReportMsgID.C2_FFT,
                       ReportMsgID.C3_POINTS, ReportMsgID.C6_PSIC):
            # Device 返回了 Report 数据
            from hif import parse_report
            return parse_report(msg_id, response["payload"])

        # 没有 Report 数据
        return None

    # ── Internal ───────────────────────────────────────────────

    def _read_frame(self, timeout_ms: int = 500) -> dict | None:
        """读取一个完整的 HIF Frame

        SPI POLL 模式下, 读取过程:
        1. 先读取最小帧头 (6B Header) 判断帧长度
        2. 根据载荷长度读取剩余部分
        """
        start = time.monotonic()
        buf = b""

        # 先读取至少 6 字节 (Header 大小)
        while len(buf) < HIF_HEADER_SIZE:
            chunk = self._transport.read(HIF_HEADER_SIZE - len(buf),
                                         timeout_ms=10)
            if chunk:
                buf += chunk
            if (time.monotonic() - start) * 1000 > timeout_ms:
                if len(buf) < HIF_HEADER_SIZE:
                    return None
                break

        # 验证 Magic
        if buf[0] != 0xA5:
            # 未对齐, 尝试跳过
            for i in range(1, len(buf)):
                if buf[i] == 0xA5 and len(buf) - i >= HIF_HEADER_SIZE:
                    buf = buf[i:]
                    break
            else:
                return None

        if len(buf) < HIF_HEADER_SIZE:
            return None

        # 从 Header 获取 Payload 长度
        # Header = [Magic(1B), Check8(1B), MsgHeader(4B)]
        msghdr_raw = buf[2:6]
        import struct
        byte0, byte1, len_seq_frag = struct.unpack("<BBH", msghdr_raw)
        payload_len = len_seq_frag & 0x0FFF
        total_size = HIF_HEADER_SIZE + payload_len + HIF_CHECKSUM_SIZE

        logger.debug(f"Frame header: MsgID=0x{byte1:02X}, payload_len={payload_len}, total={total_size}")

        # 读取剩余的 Payload + CheckSum
        remaining = total_size - len(buf)
        while remaining > 0:
            chunk = self._transport.read(remaining, timeout_ms=10)
            if chunk:
                buf += chunk
                remaining -= len(chunk)
            if (time.monotonic() - start) * 1000 > timeout_ms:
                break

        # 解包
        if len(buf) < total_size:
            logger.debug(f"Incomplete frame: got {len(buf)}/{total_size}")
            return None

        return unpack_frame(buf[:total_size])

    # ── Convenience ────────────────────────────────���───────────

    def get_version(self) -> dict | None:
        """获取设备版本信息 (MsgID=0x00)"""
        return self.send_command(CmdMsgID.VERSION_GET)

    def device_tick(self) -> dict | None:
        """设备心跳检查 (MsgID=0x01)"""
        return self.send_command(CmdMsgID.DEVICE_TICK)

    def get_sample_id(self) -> dict | None:
        """获取 Sample ID (MsgID=0x42)"""
        return self.send_command(CmdMsgID.SAMPLE_ID)
