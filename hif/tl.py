"""
HIF Transport Layer (对应 host_hif_tl.c/.h)

双线程模型:
  1. transport_task (高优先级) — 传输任务
     状态机: IDLE → WAIT_TX → TX → WAIT_RX → RX → IDLE
     负责: 唤醒 → 发送命令/轮询 → 接收数据 → 预分片处理 → 入队

  2. proc_task (普通优先级) — 报告处理任务
     从 msg_proc_queue 取消息 → 调用 HIF 层回调分发

消息队列:
  msg_order_queue: 有序接收队列 → transport_task 预处��
  msg_proc_queue:  处理队列 → proc_task 分发
"""

import time
import threading
import logging
from collections import deque
from typing import Callable, Optional
from dataclasses import dataclass, field

from llc import LLCHandle, NotifyCB
from hal import (
    sem_create, sem_delete, sem_take, sem_give,
    thread_create, delay_ms,
    timestamp_us,
    enter_critical, exit_critical,
)
from hal.types import IoValue, UploadType
from .types import (
    HifCfg, DevState, HIF_HEADER_SIZE, HIF_MAGIC, HIF_CHECKSUM_SIZE,
    SPI_WAKE_SEQ, SPI_ACK_SEQ, CmdMsgID,
)
from .frame import pack_frame, unpack_frame, check8, check32
from config import (
    CFG_TL_RETRY_MAX, CFG_TL_POLL_INTERVAL_MS,
    THREAD_PRIO_HIGH, THREAD_PRIO_NORMAL, CMD_RESP_TIMEOUT_MS,
)

logger = logging.getLogger("hif.tl")


# ── 消息项 ────────────────────────────────────────────────────
@dataclass
class MsgItem:
    """消息队列项"""
    msg_id: int
    payload: bytearray
    payload_len: int
    timestamp: int = 0
    seq: int = 0
    is_response: bool = False  # True=命令响应, False=上报

    def __post_init__(self):
        self.timestamp = timestamp_us()


# ── TL Handle ─────────────────────────────────────────────────
@dataclass
class TLHandle:
    """Transport Layer 句柄 (对应 Hif_TL_Local_t)"""

    hif_cfg: HifCfg = field(default_factory=HifCfg)
    llc_hdl: Optional[LLCHandle] = None

    # 设备状态
    dev_state: int = DevState.SLEEP
    data_ready: bool = False
    tx_seq: int = 0

    # 信号量
    task_sem: int = 0         # 传输任务信号量
    cmd_sem: int = 0          # 命令响应同步信号量
    data_sem: int = 0         # 数据就绪信号量

    # 缓冲区
    cmd_buf: Optional[bytearray] = None
    poll_buf: Optional[bytearray] = None

    # 消息队列
    msg_order_queue: deque = field(default_factory=deque)
    msg_proc_queue: deque = field(default_factory=deque)

    # 回调
    msgprev_cb: Optional[Callable] = None  # 分片预处理回调
    msgproc_cb: Optional[Callable] = None  # 处理回调 (HIF 层)

    # 线程
    transport_running: bool = False
    proc_running: bool = False
    task_hdl: any = None
    proc_task_hdl: any = None

    # ── 初始化 ────────────────────────────────────────────

    def init(self) -> int:
        """初始化 TL"""
        # 分配缓冲区
        self.cmd_buf = bytearray(self.hif_cfg.cmd_buf_len)
        self.poll_buf = bytearray(self.hif_cfg.poll_buf_len)

        # 创建信号量
        self.task_sem = sem_create(0, 10)    # 传输任务触发
        self.cmd_sem = sem_create(0, 1)       # 命令响应同步 (二值)
        # data_sem 用于通知 proc_task 有新数据, 用大上限防止 sem_give 溢出
        self.data_sem = sem_create(0, 256)

        # 创建双线程
        if self.llc_hdl:
            self.llc_hdl.notify_handle_regist(self._on_data_notify, self)

        self.transport_running = True
        self.proc_running = True

        self.task_hdl = thread_create(
            "HIF_TL_Task", self._transport_task, priority=THREAD_PRIO_HIGH
        )
        self.proc_task_hdl = thread_create(
            "HIF_TL_Proc", self._proc_task, priority=THREAD_PRIO_NORMAL
        )

        logger.info("HIF TL initialized (dual thread)")
        return 0

    def deinit(self):
        """反初始化"""
        self.transport_running = False
        self.proc_running = False
        sem_give(self.task_sem)  # 唤醒 transport_task 使其退出
        sem_give(self.data_sem)  # 唤醒 proc_task

        # 等待双线程退出
        from hal import thread_delete
        if self.task_hdl:
            thread_delete(self.task_hdl)
            self.task_hdl = None
        if self.proc_task_hdl:
            thread_delete(self.proc_task_hdl)
            self.proc_task_hdl = None

        # 清空残留消息队列, 防止 MsgItem 引用泄漏
        self.msg_order_queue.clear()
        self.msg_proc_queue.clear()

        sem_delete(self.task_sem)
        sem_delete(self.cmd_sem)
        sem_delete(self.data_sem)

    # ── 发送消息 ──────────────────────────────────────────

    def send_msg(self, msg_id: int, payload: bytes) -> int:
        """发送消息到设备

        将消息拷贝到 cmd_buf, 然后唤醒传输任务
        """
        if self.cmd_buf is None:
            return -1

        frame = pack_frame(msg_id, payload, self.tx_seq, is_command=True)
        self.tx_seq = (self.tx_seq + 1) & 0x07

        if len(frame) > len(self.cmd_buf):
            logger.error(f"Command too large: {len(frame)} > {len(self.cmd_buf)}")
            return -1

        self.cmd_buf[:len(frame)] = frame
        sem_give(self.task_sem)   # 唤醒 transport task

        return len(frame)

    def send_poll(self) -> int:
        """发送轮询命令"""
        return self.send_msg(CmdMsgID.POLL, b"")

    # ── 数据通知回调 ──────────────────────────────────────

    def _on_data_notify(self, llc_hdl: LLCHandle):
        """设备数据就绪回调 (来自 LLC 层)"""
        self.data_ready = True
        sem_give(self.data_sem)

    # ── Transport Task ─────────────────────────────────────

    def _transport_task(self):
        """传输任务主循环 (对应 HIF_TL_Transport_Task)

        状态机: IDLE → WAIT_TX → TX → WAIT_RX → RX → IDLE
        """
        logger.debug("Transport task started")

        while self.transport_running:
            # 等待信号: task_sem (有命令要发) 或 data_sem (有数据要收)
            ret = sem_take(self.task_sem, CFG_TL_POLL_INTERVAL_MS)

            if not self.transport_running:
                break

            # 检查是否有命令要发送
            if ret == 0:
                self._do_transmit()

            # 检查是否有数据就绪 (轮询或通知)
            if self.data_ready:
                self._do_receive()

            # 定时轮询 (poll_enable 模式)
            if self.hif_cfg.poll_enable and self.dev_state == DevState.ACTIVE:
                self._do_poll()

        logger.debug("Transport task exited")

    def _do_transmit(self):
        """发送命令帧"""
        if self.llc_hdl is None or self.cmd_buf is None:
            return

        try:
            n = self.llc_hdl.send(bytes(self.cmd_buf))
            logger.debug(f"TL Tx: {n} bytes, seq={self.tx_seq}")
        except Exception as e:
            logger.error(f"TL Tx error: {e}")

    def _do_receive(self):
        """接收数据 (SPI POLL 模式)"""
        if self.llc_hdl is None:
            return

        try:
            # 读取 Header 6 字节
            hdr_data = self.llc_hdl.recv(HIF_HEADER_SIZE, timeout_ms=100)
            if len(hdr_data) < HIF_HEADER_SIZE:
                return

            magic = hdr_data[0]
            if magic != HIF_MAGIC:
                logger.debug(f"Bad magic: 0x{magic:02X}")
                return

            # 解析长度
            from .frame import parse_msgheader
            hdr = parse_msgheader(hdr_data[2:6])
            payload_len = hdr["length"]
            total = HIF_HEADER_SIZE + payload_len + HIF_CHECKSUM_SIZE

            # 读取 Payload + Checksum
            remaining = total - HIF_HEADER_SIZE
            rest_data = self.llc_hdl.recv(remaining, timeout_ms=200)

            # 组装完整帧并解包
            frame_data = hdr_data + rest_data
            result = unpack_frame(frame_data)

            if result and result["valid"]:
                # 入队
                msg = MsgItem(
                    msg_id=result["header"]["msg_id"],
                    payload=bytearray(result["payload"]),
                    payload_len=len(result["payload"]),
                    seq=result["header"]["seq"],
                )

                # 分片预处理 (如果注册了)
                if self.msgprev_cb:
                    self.msgprev_cb(msg)

                # 直接入处理队列 (msg_order_queue 仅做预排序用, 此处简化)
                self.msg_proc_queue.append(msg)
                sem_give(self.data_sem)  # 唤醒 proc_task

            self.data_ready = False
        except Exception as e:
            logger.error(f"TL Rx error: {e}")

    def _do_poll(self):
        """定时轮询"""
        pass  # poll 在上层 host_driver 中通过 send_poll 触发

    # ── Report Processing Task ────────────────────────────

    def _proc_task(self):
        """报告处理任务 (对应 proc_task)

        从 msg_proc_queue 取消息 → 调用 msgproc_cb 分发
        """
        logger.debug("Report processing task started")

        while self.proc_running:
            ret = sem_take(self.data_sem, 100)  # 100ms 超时
            if not self.proc_running:
                break

            # 一次性 drain 所有待处理消息 (避免信号量计数溢出)
            while self.msg_proc_queue:
                msg = self.msg_proc_queue.popleft()
                if self.msgproc_cb:
                    try:
                        self.msgproc_cb(msg)
                    except Exception as e:
                        logger.error(f"msgproc_cb error: {e}")

        logger.debug("Report processing task exited")

    # ── SPI Wake Sequence ──────────────────────────────────

    def wake_spi(self) -> bool:
        """SPI 唤醒序列 (参考手册 3.2.1.1)

        1. Host → 0x55 0xFF 0x55 0xFF
        2. 每 1ms 读取, 等待 Device → 0x79 0x79 0x79 0x79
        3. Host → 0x79 0x79 0x79 0x79 (ack)
        """
        if self.llc_hdl is None:
            return False

        logger.info("SPI wake sequence...")

        # Step 1: 发送 wake
        self.llc_hdl.send(SPI_WAKE_SEQ)

        # Step 2: 等待 ack
        for _ in range(200):
            rx = self.llc_hdl.recv(len(SPI_ACK_SEQ), timeout_ms=5)
            if len(rx) >= len(SPI_ACK_SEQ) and rx[:len(SPI_ACK_SEQ)] == SPI_ACK_SEQ:
                break
            delay_ms(1)
        else:
            logger.error("SPI wake timeout")
            return False

        # Step 3: Host ack
        self.llc_hdl.send(SPI_ACK_SEQ)
        delay_ms(10)  # 等待 Device 稳定

        self.dev_state = DevState.ACTIVE
        logger.info("SPI wake completed, device active")
        return True
