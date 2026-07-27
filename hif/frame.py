"""
HIF Frame 编解码 (对应 host_hif_tl.c 的消息格式部分)

Frame 结构:
  ┌──────────┬──────────┬──────────────────────┬───────────────┐
  │ Magic(1B)│ Check8(1B)│ MsgHeader(4B)        │ Payload(N)   │ CheckSum(4B) │
  │ 0xA5     │           │ [Type|Req|...|MsgID] │              │ ~sum32       │
  │          │ =~sum8    │ [Length|Seq|Frag]    │              │              │
  └──────────┴──────────┴──────────────────────┴───────────────┘

MsgHeader bits (LSB first):
  Byte0: [Type(2)|Req(1)|Enc(1)|HasChk(1)|More(1)|Ext(1)|Mac(1)]
  Byte1: MsgID(8)
  Byte2-3: [Length(12)|Seq(3)|Frag(1)]
"""

import struct
import logging

from .types import (
    HIF_MAGIC, HIF_HEADER_SIZE, HIF_CHECKSUM_SIZE, HIF_MAX_PAYLOAD,
    HifType, HifReq,
)

logger = logging.getLogger("hif.frame")


# ── Checksum 计算 ─────────────────────────────────────────────

def check8(magic: int, msghdr: bytes) -> int:
    """Check8 = ~sum8(Magic + MsgHeader)"""
    return (~(magic + sum(msghdr)) & 0xFF)


def check32(msghdr: bytes, payload: bytes) -> int:
    """Check32 = ~sum32(MsgHeader + Payload), DWORD aligned"""
    data = msghdr + payload
    total = 0
    for i in range(0, len(data) - 3, 4):
        total = (total + struct.unpack_from("<I", data, i)[0]) & 0xFFFFFFFF
    rem = len(data) % 4
    if rem:
        last = data[-rem:] + b"\x00" * (4 - rem)
        total = (total + struct.unpack("<I", last)[0]) & 0xFFFFFFFF
    return (~total & 0xFFFFFFFF)


# ── MsgHeader 构建/解析 ──────────────────────────────────────

def build_msgheader(frame_type: HifType, req: HifReq, msg_id: int,
                    payload_len: int, seq: int = 0, frag: int = 0,
                    has_checksum: int = 1, mac: int = 0, enc: int = 0) -> bytes:
    """构建 MsgHeader 4字节"""
    byte0 = ((frame_type & 0x03) |
             ((req & 0x01) << 2) |
             ((enc & 0x01) << 3) |
             ((has_checksum & 0x01) << 4) |
             ((mac & 0x01) << 7))
    byte1 = msg_id & 0xFF
    len_seq_frag = ((payload_len & 0x0FFF) |
                    ((seq & 0x07) << 12) |
                    ((frag & 0x01) << 15))
    return struct.pack("<BBH", byte0, byte1, len_seq_frag)


def parse_msgheader(data: bytes) -> dict:
    """解析 MsgHeader 4字节"""
    byte0, byte1, len_seq_frag = struct.unpack("<BBH", data[:4])
    return {
        "type": byte0 & 0x03,
        "req": (byte0 >> 2) & 0x01,
        "enc": (byte0 >> 3) & 0x01,
        "has_checksum": (byte0 >> 4) & 0x01,
        "more": (byte0 >> 5) & 0x01,
        "ext": (byte0 >> 6) & 0x01,
        "mac": (byte0 >> 7) & 0x01,
        "msg_id": byte1,
        "length": len_seq_frag & 0x0FFF,
        "seq": (len_seq_frag >> 12) & 0x07,
        "frag": (len_seq_frag >> 15) & 0x01,
    }


# ── Frame 打包/解包 ──────────────────────────────────────────

def pack_frame(msg_id: int, payload: bytes, seq: int = 0,
               is_command: bool = True) -> bytes:
    """打包完整 HIF Frame"""
    frame_type = HifType.HOST_TO_DEVICE if is_command else HifType.DEVICE_TO_HOST
    req = HifReq.COMMAND if is_command else HifReq.RESPONSE

    msghdr = build_msgheader(frame_type, req, msg_id, len(payload), seq)

    ck8 = check8(HIF_MAGIC, msghdr)
    header = struct.pack("<BB", HIF_MAGIC, ck8) + msghdr

    ck32 = check32(msghdr, payload)

    return header + payload + struct.pack("<I", ck32)


def unpack_frame(data: bytes) -> dict | None:
    """解包 HIF Frame

    Returns:
        {
            "magic": int, "check8": int, "check8_ok": bool,
            "header": {...}, "payload": bytes, "checksum": int,
            "valid": bool,
        }
        None if magic mismatch or incomplete
    """
    if len(data) < HIF_HEADER_SIZE + HIF_CHECKSUM_SIZE:
        return None

    magic = data[0]
    if magic != HIF_MAGIC:
        return None

    check8_recv = data[1]
    msghdr = data[2:6]

    hdr = parse_msgheader(msghdr)
    payload_len = hdr["length"]
    total_size = HIF_HEADER_SIZE + payload_len + HIF_CHECKSUM_SIZE

    if len(data) < total_size:
        return None

    payload = data[6:6 + payload_len]
    checksum_recv = struct.unpack("<I", data[6 + payload_len:6 + payload_len + 4])[0]

    check8_ok = (check8_recv == check8(HIF_MAGIC, msghdr))
    expected_ck32 = check32(msghdr, payload)
    valid = (checksum_recv == expected_ck32)

    return {
        "magic": magic,
        "check8": check8_recv,
        "check8_ok": check8_ok,
        "header": hdr,
        "payload": payload,
        "checksum": checksum_recv,
        "valid": valid,
    }
