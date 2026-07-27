"""
HIF 协议层入口

分层:
  hif/types.py   — 协议类型与常量
  hif/frame.py   — Frame 编解码与校验
  hif/tl.py      — Transport Layer (双线程: transport + report processing)
  hif/hif.py     — HIF 层 (命令/响应、报告回调、分片重组)
  hif/reports.py — Report 数据解析 (C1/C2/C3/C6)
"""

from .types import (
    HIF_MAGIC, HIF_HEADER_SIZE, HIF_CHECKSUM_SIZE,
    HIF_MAX_PAYLOAD, HIF_FRAME_MAX_SIZE,
    HifType, HifReq, CmdMsgID, ReportMsgID, CmdStatus, DevState,
    HifCfg,
    SPI_WAKE_SEQ, SPI_ACK_SEQ,
)
from .frame import pack_frame, unpack_frame, check8, check32
from .tl import TLHandle, MsgItem
from .hif import HIFHandle, MsgReportCB
from .reports import parse_report

__all__ = [
    "HIF_MAGIC", "HIF_HEADER_SIZE", "HIF_CHECKSUM_SIZE",
    "HIF_MAX_PAYLOAD", "HIF_FRAME_MAX_SIZE",
    "HifType", "HifReq", "CmdMsgID", "ReportMsgID", "CmdStatus", "DevState",
    "HifCfg",
    "SPI_WAKE_SEQ", "SPI_ACK_SEQ",
    "pack_frame", "unpack_frame", "check8", "check32",
    "TLHandle", "MsgItem",
    "HIFHandle", "MsgReportCB",
    "parse_report",
]
