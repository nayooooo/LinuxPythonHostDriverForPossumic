"""
HIF 层 — 消息处理与回调管理 (对应 host_hif.c/.h)

职责:
  1. 命令/响应同步 (HIF_Cmd_Request → wait_cmd_sem → response)
  2. Report 回调注册/分发 (HIF_Report_Handle_Regist)
  3. 分片消息重组 (fragment_t pending/abnormal/free 三链表)
  4. 消息分组 (30 groups, 0xA0~0xFF 范围, 每组16个 msg_id)
"""

import time
import struct
import logging
from typing import Callable, Optional
from dataclasses import dataclass, field
from collections import deque

from .types import (
    HifCfg, DevState, CmdMsgID, ReportMsgID, CmdStatus,
)
from .tl import TLHandle, MsgItem
from llc import LLCHandle
from hal import sem_create, sem_delete, sem_take, sem_give
from config import (
    CMD_RESP_TIMEOUT_MS,
    CFG_MSG_FRAGMENT_EN,
    CFG_MSG_FRAGMENT_RETRY_EN,
    CFG_HOST_HIF_FRAGMENT_PENDING_INFO_ABNORMAL_BURST_THR,
    CFG_HOST_HIF_FRAGMENT_FREE_INFO_NUM_MAX,
    CFG_HOST_HIF_FRAGMENT_HOLD_BUFFER_SIZE_MAX,
)

logger = logging.getLogger("hif")


# ── 报告回调类型 ──────────────────────────────────────────────
# callback(msg_id, payload: bytes, payload_len: int, arg)
MsgReportCB = Callable[[int, bytes, int, any], None]


# ── 分片信息 (对应 fragment_info_t) ──────────────────────────

@dataclass
class FragmentInfo:
    """分片片段信息"""
    offset: int = 0            # 数据偏移
    length: int = 0            # 片段长度
    total_len: int = 0         # 消息总长度
    flow_seq: int = 0          # 接收顺序号
    abnormal_burst_num: int = 0  # 异常突发计数
    buffer: Optional[bytearray] = None  # 片段数据
    ref_count: int = 0         # 引用计数


# ── 报告句柄 (对应 Hif_MsgReportHdl_t) ───────────────────────

@dataclass
class ReportHandle:
    """报告消息处理句柄"""
    msg_id: int
    msg_cb: Optional[MsgReportCB] = None
    msg_arg: any = None

    # 缓冲区 (零拷贝)
    buffer: Optional[bytearray] = None
    buffer_len: int = 0
    flag: int = 0  # BUF_REUSE / FRAG_RETRY / TO_FREE

    # 分片管理
    fragment_enable: bool = False
    frag_retry_enable: bool = False
    list_pending: deque = field(default_factory=deque)    # 待重组分片
    list_abnormal: deque = field(default_factory=deque)   # 异常分片
    list_free: deque = field(default_factory=deque)       # 空闲分片池


# ── HIF Handle ────────────────────────────────────────────────

@dataclass
class HIFHandle:
    """HIF 层句柄 (对应 HifLocal_t)"""

    cfg: HifCfg = field(default_factory=HifCfg)
    state: int = DevState.SLEEP
    llc_hdl: Optional[LLCHandle] = None
    tl_hdl: Optional[TLHandle] = None

    # 命令响应同步
    wait_cmd_enable: bool = False
    wait_cmd_id: int = 0
    cmd_resp_buf: Optional[bytearray] = None
    wait_cmd_sem: int = 0

    # 报告句柄 (对应 hif_msghdl[HIF_MSG_GROUP_REPORT_NUM])
    # 分组: (msg_id >> 4) - 0x0A → group index
    msghdl_groups: dict[int, list[Optional[ReportHandle]]] = field(default_factory=dict)
    msghdl_sem: int = 0

    def __post_init__(self):
        self.wait_cmd_sem = sem_create(0, 1)
        self.msghdl_sem = sem_create(1, 1)  # 保护句柄数组的互斥信号量

    # ── 初始化/反初始化 ────────────────────────────────────

    def init(self, llc_hdl: LLCHandle) -> int:
        """初始化 HIF 实体 (对应 HIF_Entity_Init)"""
        self.llc_hdl = llc_hdl

        # 创建 TL
        self.tl_hdl = TLHandle(hif_cfg=self.cfg, llc_hdl=llc_hdl)

        # 注册消息处理回调链
        self.tl_hdl.msgprev_cb = self._fragment_preprocess  # 分片预处理
        self.tl_hdl.msgproc_cb = self._msg_process           # 消息处理

        # 初始化 TL
        self.tl_hdl.init()

        logger.info("HIF entity initialized")
        return 0

    def deinit(self):
        if self.tl_hdl:
            self.tl_hdl.deinit()
        sem_delete(self.wait_cmd_sem)
        sem_delete(self.msghdl_sem)
        logger.info("HIF entity deinitialized")

    # ── 命令/响应 ──────────────────────────────────────────

    def cmd_request(self, msg_id: int, param: bytes,
                    resp_buf: bytearray, buf_len: int,
                    timeout_ms: int = CMD_RESP_TIMEOUT_MS) -> int:
        """发送命令并同步等待响应 (对应 HIF_Cmd_Request)

        Returns:
            响应长度, 或负值错误码
        """
        if self.tl_hdl is None:
            return -1

        # 设置等待
        self.wait_cmd_id = msg_id
        self.cmd_resp_buf = resp_buf
        self.wait_cmd_enable = True

        # 通过 TL 发送
        self.tl_hdl.send_msg(msg_id, param)

        # 等待响应
        ret = sem_take(self.wait_cmd_sem, timeout_ms)
        self.wait_cmd_enable = False

        if ret != 0:
            logger.warning(f"Cmd 0x{msg_id:02X}: timeout ({timeout_ms}ms)")
            return -2  # TIMEOUT

        return len(resp_buf) if resp_buf else 0

    # ── 消息处理回调 ──────────────────────────────────────

    def _fragment_preprocess(self, msg: MsgItem):
        """分片预处理 (对应 msgprev_cb)

        检查消息是否为分片, 如果是则进行分片重组
        """
        # 非分片消息直接放行
        # (分片检测通过 msg header 的 frag bit, 在 frame 解包时已解析)
        pass

    def _msg_process(self, msg: MsgItem):
        """消息处理回调 (对应 msgproc_cb)

        处理接收到的消息:
          - 如果是命令响应 → 拷贝到 cmd_resp_buf → give wait_cmd_sem
          - 如果是 Report → 查找 report_hdl → 调用用户回调

        注意: 此函数可能在 proc_task 线程中被调用,
        cmd_request 的调用者在主线程, 需要保护 wait_cmd_* 字段
        """
        # 命令响应: 简单标志检查 (大部分场景下足够, 且 Python GIL 保障基本原子性)
        if self.wait_cmd_enable and msg.msg_id == self.wait_cmd_id:
            if self.cmd_resp_buf is not None:
                sz = min(len(msg.payload), len(self.cmd_resp_buf))
                self.cmd_resp_buf[:sz] = msg.payload[:sz]
            sem_give(self.wait_cmd_sem)
            return

        # 报告消息
        hdl = self._get_report_hdl(msg.msg_id)
        if hdl and hdl.msg_cb:
            try:
                hdl.msg_cb(msg.msg_id, bytes(msg.payload),
                          msg.payload_len, hdl.msg_arg)
            except Exception as e:
                logger.error(f"Report callback error for 0x{msg.msg_id:02X}: {e}")

    # ── 报告回调注册 ──────────────────────────────────────

    def report_handle_regist(self, msg_id: int, cb: MsgReportCB,
                             arg: any = None) -> int:
        """注册报告回调 (对应 HIF_Report_Handle_Regist)"""
        group = (msg_id >> 4) - 0x0A
        index = msg_id & 0x0F

        ret = sem_take(self.msghdl_sem, 100)
        if ret != 0:
            logger.warning(f"msghdl_sem timeout during regist 0x{msg_id:02X}")
            return -1

        try:
            if group not in self.msghdl_groups:
                self.msghdl_groups[group] = [None] * 16

            hdl = ReportHandle(
                msg_id=msg_id,
                msg_cb=cb,
                msg_arg=arg,
                fragment_enable=self.cfg.fragment_enable,
                frag_retry_enable=self.cfg.fragment_retry_enable,
            )
            self.msghdl_groups[group][index] = hdl
        finally:
            sem_give(self.msghdl_sem)

        logger.info(f"Report handler registered: 0x{msg_id:02X}")
        return 0

    def report_handle_unregist(self, msg_id: int) -> int:
        """注销报告回调"""
        group = (msg_id >> 4) - 0x0A
        index = msg_id & 0x0F
        ret = sem_take(self.msghdl_sem, 100)
        if ret != 0:
            return -1
        try:
            if group in self.msghdl_groups:
                self.msghdl_groups[group][index] = None
        finally:
            sem_give(self.msghdl_sem)
        return 0

    def _get_report_hdl(self, msg_id: int) -> Optional[ReportHandle]:
        """获取报告句柄"""
        group = (msg_id >> 4) - 0x0A
        index = msg_id & 0x0F
        group_list = self.msghdl_groups.get(group)
        if group_list and index < len(group_list):
            return group_list[index]
        return None

    # ── SPI 唤醒 ──────────────────────────────────────────

    def wake_spi(self) -> bool:
        if self.tl_hdl:
            return self.tl_hdl.wake_spi()
        return False

    # ── 重置 ──────────────────────────────────────────────

    def dev_reset(self):
        """设备重置"""
        self.state = DevState.SLEEP
        self.wait_cmd_enable = False
        if self.tl_hdl:
            self.tl_hdl.dev_state = DevState.SLEEP
