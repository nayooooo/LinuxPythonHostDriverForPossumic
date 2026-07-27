"""
HIF 协议帧打包/解包模块

HIF Frame 结构:
┌──────────┬──────────────────┬──────────┬───────────┐
│ Magic(1B)│ Check8(1B)       │ MsgHeader(4B)        │ ← Header(6B)
│  0xA5    │ =~sum8(hdr[0]+hdr[2..5])                │
├──────────┼──────────────────┼───────────────────────┤
│ Payload(0~4095B)                                   │
├────────────────────────────────────────────────────┤
│ CheckSum(4B)  = ~sum32(MsgHeader[2..5] + Payload) │
│ (DWORD aligned, little-endian)                    │
└────────────────────────────────────────────────────┘

MsgHeader[4B] 结构 (bits, LSB first):
  Byte0: [Type(2)|Req(1)|Enc(1)|HasCheck(1)|More(1)|Ext(1)|Mac(1)]
  Byte1: MsgID(8)
  Byte2-3: [Length(12)|Seq(3)|Frag(1)]

校验:
  Check8 = ~sum8(Magic + MsgHeader)          — 头校验
  Check32 = ~sum32(MsgHeader + Payload)      — 帧校验 (DWORD对齐)
  MAC32 = (当 Mac=1 时的认证校验, 暂不支持)
"""

import struct
from .types import (
    HIF_MAGIC, HIF_HEADER_SIZE, HIF_CHECKSUM_SIZE,
    HIF_MAX_PAYLOAD, HIF_FRAME_MAX_SIZE,
    HifType, HifReq, CmdMsgID, ReportMsgID,
)


def _check8(data: bytes) -> int:
    """计算 Check8: ~sum8 of byte sequence (取反的8位和)"""
    return (~sum(data) & 0xFF)


def _check32(data: bytes) -> int:
    """计算 Check32: ~sum32 of DWORD-aligned data"""
    # 按 DWORD(32bit) 遍历
    total = 0
    for i in range(0, len(data) - 3, 4):
        dword = struct.unpack_from("<I", data, i)[0]
        total = (total + dword) & 0xFFFFFFFF
    # 处理尾部不足 4 字节的部分，补 0
    remainder = len(data) % 4
    if remainder:
        last = data[-remainder:] + b'\x00' * (4 - remainder)
        total = (total + struct.unpack("<I", last)[0]) & 0xFFFFFFFF
    return (~total & 0xFFFFFFFF)


def build_msgheader(frame_type: HifType, req: HifReq, msg_id: int,
                    payload_len: int, seq: int = 0, frag: int = 0,
                    has_checksum: int = 1, mac: int = 0, enc: int = 0) -> bytes:
    """构建 MsgHeader 4字节 (little-endian)

    Args:
        frame_type: HifType (Host→Device=1, Device→Host=2)
        req: HifReq (Command=1, Response=0)
        msg_id: 消息ID
        payload_len: Payload 长度 (0~4095)
        seq: 序列号 (0~7)
        frag: 分片标志 (0=单帧, 1=分片)
        has_checksum: 1=帧尾有4字节CheckSum, 0=无
        mac: 0=Check32(~sum32), 1=MAC32认证校验
        enc: 加密标志
    """
    byte0 = ((frame_type & 0x03) |
             ((req & 0x01) << 2) |
             ((enc & 0x01) << 3) |
             ((has_checksum & 0x01) << 4) |
             ((mac & 0x01) << 7))
    byte1 = msg_id & 0xFF
    len_seq_frag = (payload_len & 0x0FFF) | ((seq & 0x07) << 12) | ((frag & 0x01) << 15)
    return struct.pack("<BBH", byte0, byte1, len_seq_frag)


def parse_msgheader(data: bytes) -> dict:
    """解析 MsgHeader 4字节"""
    byte0, byte1, len_seq_frag = struct.unpack("<BBH", data[:4])
    return {
        "type": byte0 & 0x03,               # HifType
        "req": (byte0 >> 2) & 0x01,          # 1=Command, 0=Response
        "enc": (byte0 >> 3) & 0x01,
        "has_checksum": (byte0 >> 4) & 0x01, # 1=帧尾有CheckSum
        "more": (byte0 >> 5) & 0x01,
        "ext": (byte0 >> 6) & 0x01,
        "mac": (byte0 >> 7) & 0x01,          # 0=Check32, 1=MAC32
        "msg_id": byte1,
        "length": len_seq_frag & 0x0FFF,
        "seq": (len_seq_frag >> 12) & 0x07,
        "frag": (len_seq_frag >> 15) & 0x01,
    }


def pack_frame(msg_id: int, payload: bytes, seq: int = 0,
               is_command: bool = True) -> bytes:
    """打包 HIF 帧

    Args:
        msg_id: 消息ID
        payload: Payload 数据 (0~4095字节)
        seq: 序列号 (0~7)
        is_command: True=Host→Device命令, False=Device→Host响应

    Returns:
        完整的 HIF 帧字节流
    """
    frame_type = HifType.HOST_TO_DEVICE if is_command else HifType.DEVICE_TO_HOST
    req = HifReq.COMMAND if is_command else HifReq.RESPONSE

    # 构建 MsgHeader (check_mode=0, enc=0)
    msghdr = build_msgheader(frame_type, req, msg_id, len(payload), seq)

    # 计算 Check8 = ~sum8(Magic + MsgHeader)
    ck8 = _check8(bytes([HIF_MAGIC]) + msghdr)

    # 构建 Header
    header = struct.pack("<BB", HIF_MAGIC, ck8) + msghdr

    # 计算 Check32 = ~sum32(MsgHeader + Payload)
    ck32 = _check32(msghdr + payload)

    return header + payload + struct.pack("<I", ck32)


def unpack_frame(data: bytes) -> dict | None:
    """解包 HIF 帧

    Returns:
        dict: 解析结果 或 None (校验失败)
        {
            "magic": int,
            "check8": int,
            "header": {type, req, msg_id, length, seq, frag, ...},
            "payload": bytes,
            "checksum": int,
            "valid": bool,
        }
    """
    if len(data) < HIF_HEADER_SIZE + HIF_CHECKSUM_SIZE:
        return None

    magic = data[0]
    check8_recv = data[1]
    msghdr = data[2:6]

    # 验证 Magic
    if magic != HIF_MAGIC:
        return None

    # 解析 MsgHeader
    hdr = parse_msgheader(msghdr)
    payload_len = hdr["length"]
    total_size = HIF_HEADER_SIZE + payload_len + HIF_CHECKSUM_SIZE

    if len(data) < total_size:
        return None

    payload = data[6:6 + payload_len]
    checksum_recv = struct.unpack("<I", data[6 + payload_len:6 + payload_len + 4])[0]

    # 验证 Check8 (弱校验, 有时会被传输噪声破坏)
    check8_ok = (check8_recv == _check8(data[:1] + msghdr))

    # 验证 Check32
    expected_ck32 = _check32(msghdr + payload)
    valid = (checksum_recv == expected_ck32)

    return {
        "magic": magic,
        "check8": check8_recv,
        "check8_ok": check8_ok,
        "header": hdr,
        "payload": payload,
        "checksum": checksum_recv,
        "valid": valid and check8_ok,
    }


def parse_response_status(payload: bytes) -> int:
    """解析 Response Payload 的状态码 (第1字节)"""
    if len(payload) >= 1:
        return payload[0]
    return -1
