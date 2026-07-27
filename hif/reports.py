"""
HIF Report 消息解析

Report 是 Device 主动上报给 Host 的数据帧:
- 0xC1: 1D Range FFT (Cube数据)
- 0xC2: 2D Range-Doppler FFT
- 0xC3: 目标检测点 (Point Cloud)
- 0xC6: PSIC 自定义 Debug 数据
"""

import struct
import logging

logger = logging.getLogger(__name__)


# ── 0xC1: 1D Range FFT ─────────────────────────────────────────────
def parse_c1_fft(payload: bytes) -> dict:
    """解析 C1 Range FFT 数据

    Payload 结构 (每 32bit 一组):
      Frame Index (DWORD)
      Frame Length (DWORD)
      Data Offset (DWORD)
      FFT bin[0..N] (DWORD) 每个 bin: imag(16bit) | real(16bit)
    """
    if len(payload) < 12:
        return {"error": "payload too short"}

    frame_index, frame_len, data_offset = struct.unpack_from("<III", payload, 0)
    fft_raw = payload[12:]
    fft_bins = []

    for i in range(0, len(fft_raw) - 3, 4):
        dword = struct.unpack_from("<I", fft_raw, i)[0]
        real = _sign16(dword & 0xFFFF)
        imag = _sign16((dword >> 16) & 0xFFFF)
        fft_bins.append({"real": real, "imag": imag, "index": i // 4})

    return {
        "msg_type": "C1_FFT",
        "frame_index": frame_index,
        "frame_length": frame_len,
        "data_offset": data_offset,
        "fft_bins": fft_bins,
        "bin_count": len(fft_bins),
    }


# ── 0xC2: 2D Range-Doppler FFT ─────────────────────────────────────
def parse_c2_fft(payload: bytes) -> dict:
    """解析 C2 Range-Doppler FFT 数据

    Payload 结构:
      Frame Index (DWORD)
      Frame Length (DWORD)
      Data Offset (DWORD)
      FFT Info (可变长度):
        Type(u8) | TotalLength(u24) | Tx_num(u8) | Rx_num(u8) | Reserved(2)
        Range bin num(u16) | Dop bin num(u16)
      FFT bin[0..N] (DWORD): imag(16bit) | real(16bit)
    """
    if len(payload) < 12:
        return {"error": "payload too short"}

    frame_index, frame_len, data_offset = struct.unpack_from("<III", payload, 0)

    # 解析 FFT Info (从偏移12开始)
    info_data = payload[12:]
    if len(info_data) < 12:
        return {"error": "FFT info too short"}

    type_u8 = info_data[0]
    total_length = info_data[0] | (info_data[1] << 8) | (info_data[2] << 16)
    tx_num = info_data[3]
    rx_num = info_data[4]
    # reserved bytes at 5,6
    range_bins = struct.unpack_from("<H", info_data, 7)[0]
    dop_bins = struct.unpack_from("<H", info_data, 9)[0]

    # FFT data starts after info + padding
    info_len = 11  # Type(1)+TotalLength(3)+Tx(1)+Rx(1)+Reserved(2)+Range(2)+Dop(2)
    fft_raw = info_data[info_len:]

    fft_bins = []
    for i in range(0, len(fft_raw) - 3, 4):
        dword = struct.unpack_from("<I", fft_raw, i)[0]
        real = _sign16(dword & 0xFFFF)
        imag = _sign16((dword >> 16) & 0xFFFF)
        fft_bins.append({"real": real, "imag": imag, "index": i // 4})

    return {
        "msg_type": "C2_FFT",
        "frame_index": frame_index,
        "frame_length": frame_len,
        "data_offset": data_offset,
        "type": type_u8,
        "total_length": total_length,
        "tx_num": tx_num,
        "rx_num": rx_num,
        "range_bins": range_bins,
        "dop_bins": dop_bins,
        "fft_bins": fft_bins,
        "bin_count": len(fft_bins),
    }


# ── 0xC3: 目标检测点 ───────────────────────────────────────────────
def parse_c3_points(payload: bytes) -> dict:
    """解析 C3 目标检测点数据

    Payload 结构:
      Frame Index (DWORD)
      Frame Length (DWORD)
      Data Offset (DWORD)
      Points:
        Type(8bit): 低4bit=类型, bit0=flag
        Total Length(16bit)
        Range/X(cm) (有符号)
        Azimuth/Y (0.01°) (有符号)
        Elevation/Z (0.01°) (有符号)
        SNR (0.01dB) (有符号)
        Reserved
    """
    if len(payload) < 12:
        return {"error": "payload too short"}

    frame_index, frame_len, data_offset = struct.unpack_from("<III", payload, 0)
    points_data = payload[12:]
    point_size = 16  # 每个点16字节: Type(1)+TotalLen(2)+Range(4)+Azimuth(4)+Elev(4)+SNR(2)+Reserved(2)=19? 需要确认
    # 根据手册推测: Type(1)+TotalLen(2)+Range/X(4)+Azimuth/Y(4)+Elev/Z(4)+SNR(2)+Reserved(2) = 19?
    # 实际按16字节对齐: Type(1)+Len(2)+Range(4)+Azimuth(4)+Elev(4)+SNR(1)+Reserved(0)? = 16
    # 使用16字节每点
    
    points = []
    for i in range(0, len(points_data) - point_size + 1, point_size):
        pt = points_data[i:i + point_size]
        type_byte = pt[0]
        total_len = struct.unpack_from("<H", pt, 0)[0] & 0xFFFF
        range_val = struct.unpack_from("<i", pt, 4)[0]
        azimuth = struct.unpack_from("<i", pt, 8)[0]
        elevation = struct.unpack_from("<i", pt, 12)[0]
        # SNR packed in remaining bytes
        snr = struct.unpack_from("<h", pt, 16)[0] if len(pt) >= 18 else 0

        points.append({
            "type": type_byte & 0x0F,
            "flag": (type_byte >> 7) & 0x01,
            "total_length": total_len & 0xFFFF,
            "range_cm": range_val,           # or X(cm)
            "azimuth_001deg": azimuth,       # or Y(cm), 0.01度
            "elevation_001deg": elevation,   # or Z(cm), 0.01度
            "snr_001db": snr,                # 0.01dB
        })

    return {
        "msg_type": "C3_POINTS",
        "frame_index": frame_index,
        "frame_length": frame_len,
        "data_offset": data_offset,
        "points": points,
        "point_count": len(points),
    }


# ── 0xC6: PSIC 自定义数据 ──────────────────────────────────────────
def parse_c6_psic(payload: bytes) -> dict:
    """解析 C6 PSIC 自定义 Debug 数据

    Payload 结构:
      Dim (4B)
      Len (4B)
      Align Mode (4B)
      Q (4B)
      Data Type (4B): byte=0/short=1/word=2/float=3/double=4
      Sign (4B)
      Payload Name (以\0结尾的字符串)
      Payload Data (Dim*Len*DataSize, 按Align Mode排列)
    """
    if len(payload) < 24:
        return {"error": "payload too short"}

    dim, length, align_mode, q, data_type, sign = struct.unpack_from("<iiiiii", payload, 0)

    # 找 Payload Name (\0结尾)
    name_start = 24
    name_end = payload.find(b'\x00', name_start)
    if name_end == -1:
        name_end = name_start + 32  # fallback

    payload_name = payload[name_start:name_end].decode("utf-8", errors="replace")
    data_start = name_end + 1

    # 推断数据大小
    type_map = {0: 1, 1: 2, 2: 4, 3: 4, 4: 8}  # byte/short/word/float/double
    elem_size = type_map.get(data_type, 4)
    total_count = dim * length
    data_end = data_start + total_count * elem_size
    raw_data = payload[data_start:data_end]

    return {
        "msg_type": "C6_PSIC",
        "dim": dim,
        "length": length,
        "align_mode": align_mode,
        "q": q,
        "data_type": data_type,
        "sign": sign,
        "payload_name": payload_name,
        "raw_data": raw_data,
        "parsed_data": _parse_c6_data(raw_data, data_type, sign, dim, length, align_mode, q),
    }


# ── Helper ──────────────────────────────────────────────────────────
def _sign16(val: int) -> int:
    """16位有符号扩展"""
    return val if val < 0x8000 else val - 0x10000


def _parse_c6_data(raw: bytes, dtype: int, sign: int,
                   dim: int, length: int, align_mode: int, q: int) -> dict:
    """解析 C6 数据为 Python 数值"""
    fmt_map = {0: "b" if sign else "B", 1: "h" if sign else "H",
               2: "i" if sign else "I", 3: "f", 4: "d"}
    fmt = fmt_map.get(dtype, "i")
    elem_size = struct.calcsize(fmt)
    count = len(raw) // elem_size

    try:
        values = list(struct.unpack(f"<{count}{fmt}", raw[:count * elem_size]))
    except struct.error:
        values = list(raw)

    # 除 Q 因子
    if q > 0:
        values = [v / (1 << q) for v in values]

    return {
        "dim": dim,
        "length": length,
        "align_mode": align_mode,
        "q": q,
        "data_type": dtype,
        "values": values,
        "count": count,
    }


# ── Report 分发器 ──────────────────────────────────────────────────
REPORT_PARSERS = {
    0xC1: parse_c1_fft,
    0xC2: parse_c2_fft,
    0xC3: parse_c3_points,
    0xC6: parse_c6_psic,
}


def parse_report(msg_id: int, payload: bytes) -> dict | None:
    """根据 msg_id 自动分发解析"""
    parser = REPORT_PARSERS.get(msg_id)
    if parser:
        try:
            return parser(payload)
        except Exception as e:
            logger.error(f"Failed to parse report 0x{msg_id:02X}: {e}")
            return {"error": str(e), "msg_type": f"0x{msg_id:02X}", "raw": payload}
    return {"msg_type": f"UNKNOWN_0x{msg_id:02X}", "raw": payload}
