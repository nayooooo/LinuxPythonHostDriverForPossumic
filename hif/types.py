"""
HIF 协议类型定义 (对应 hif_type.h + 原 types.py)
"""

import enum


# ── HIF Frame 常量 ────────────────────────────────────────────
HIF_MAGIC           = 0xA5
HIF_HEADER_SIZE     = 6     # Magic(1) + Check8(1) + MsgHeader(4)
HIF_CHECKSUM_SIZE   = 4     # Check32
HIF_MAX_PAYLOAD     = 4095
HIF_FRAME_MAX_SIZE  = HIF_HEADER_SIZE + HIF_MAX_PAYLOAD + HIF_CHECKSUM_SIZE

# ── HIF 帧类型 ────────────────────────────────────────────────
class HifType(enum.IntEnum):
    HOST_TO_HOST    = 0   # 内部使用
    HOST_TO_DEVICE  = 1   # Command
    DEVICE_TO_HOST  = 2   # Response / Report

class HifReq(enum.IntEnum):
    RESPONSE = 0   # Device→Host
    COMMAND  = 1   # Host→Device

# ── Command MsgID ─────────────────────────────────────────────
class CmdMsgID(enum.IntEnum):
    VERSION_GET     = 0x00   # 获取设备信息
    DEVICE_TICK     = 0x01   # 心跳
    CONNECT         = 0x05   # 设备连接
    GENERAL_CMD     = 0x07   # 通用命令 (子命令在 payload 中)
    POLL            = 0x0C   # Host 轮询
    SAMPLE_ID       = 0x42   # 获取 Sample ID
    IO_SAMPLE_ID    = 0x60   # IO 模式 Sample ID

# ── Report MsgID ──────────────────────────────────────────────
class ReportMsgID(enum.IntEnum):
    C1_FFT          = 0xC1   # 1D Range FFT
    C2_FFT          = 0xC2   # 2D Range-Doppler FFT
    C3_POINTS       = 0xC3   # 目标检测点
    C6_PSIC         = 0xC6   # PSIC Debug 数据

# ── 响应状态码 ────────────────────────────────────────────────
class CmdStatus(enum.IntEnum):
    SUCCESS    = 0
    UNSUPPORT  = 1
    VERSION    = 2
    TOOLONG    = 3
    CHECK      = 4
    PARAM      = 5
    REPLAY     = 6
    AUTH       = 7
    LATE       = 8
    BUSY       = 9
    STATE      = 10
    SYSERR     = 11
    IO         = 12

# ── 设备状态 ────────────────────────────────────────────────
class DevState(enum.IntEnum):
    SLEEP       = 0
    WAIT_ACTIVE = 1
    ACTIVE      = 2

# ── SPI 唤醒 ─────────────────────────────────────────────────
SPI_WAKE_SEQ = bytes([0x55, 0xFF, 0x55, 0xFF])
SPI_ACK_SEQ  = bytes([0x79, 0x79, 0x79, 0x79])

# ── HIF 配置 (对应 HifCfg_t) ──────────────────────────────────
from dataclasses import dataclass

@dataclass
class HifCfg:
    """HIF 配置参数"""
    poll_enable: bool = True            # 轮询使能
    app_retry_enable: bool = True       # 应用层重试
    tl_retry_enable: bool = True        # TL 重传
    fragment_enable: bool = True        # 分片使能
    fragment_retry_enable: bool = True  # 分片重传
    cmd_buf_len: int = 512              # 命令缓冲区长度
    poll_buf_len: int = 512             # 轮询缓冲区长度
