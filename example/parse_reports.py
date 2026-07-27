#!/usr/bin/env python3
"""
Report 解析示例 — 根据 msg_id 解析 C1/C2/C3/C6 各类雷达数据

C1: 1D Range FFT (距离维 FFT)
C2: 2D Range-Doppler FFT (距离-多普勒 FFT)
C3: 目标检测点云 (点云数据)
C6: PSIC Debug 数据

用法 (开发板):
    python example/parse_reports.py /dev/spidev0.0
"""

import sys
import os
import struct
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from host_driver import RadarReceiver, MSGID_C1_FFT, MSGID_C2_FFT, MSGID_C3_POINTS, MSGID_C6_PSIC


# ====================================================================
#  Report 解析函数
# ====================================================================

def parse_c1_fft(payload: bytes) -> dict:
    """解析 C1 1D Range FFT 数据

    C1 数据格式:
      [frame_index:2B][frame_len:2B][data_offset:2B][bins: N *(real:2B, imag:2B)]
    """
    frame_index, frame_len, data_offset = struct.unpack_from("<HHH", payload, 0)
    bin_data = payload[6:]
    bins = []
    for i in range(0, len(bin_data), 4):
        if i + 4 <= len(bin_data):
            real, imag = struct.unpack_from("<hh", bin_data, i)
            bins.append((real, imag))
    return {
        "type": "C1 Range FFT",
        "frame_index": frame_index,
        "frame_len": frame_len,
        "data_offset": data_offset,
        "bin_count": len(bins),
        "bins": bins[:10],  # 只返回前 10 个 bin 用于显示
    }


def parse_c2_fft(payload: bytes) -> dict:
    """解析 C2 2D Range-Doppler FFT 数据

    C2 数据格式:
      [tx:1B][rx:1B][range_bins:1B][dop_bins:1B][data: range_bins * dop_bins * 4B]
    """
    tx, rx, range_bins, dop_bins = struct.unpack_from("<BBBB", payload, 0)
    data_len = range_bins * dop_bins * 4
    data = payload[4:4 + data_len]
    return {
        "type": "C2 Doppler FFT",
        "tx": tx, "rx": rx,
        "range_bins": range_bins,
        "dop_bins": dop_bins,
        "data_size": len(data),
    }


def parse_c3_points(payload: bytes) -> dict:
    """解析 C3 目标检测点云

    C3 每点格式 (可变长, 典型 ~16 字节):
      [range_cm:4B][azimuth_001deg:2B][elevation_001deg:2B]
      [snr_001db:2B][doppler_001ms:2B][...]
    """
    POINT_STRUCT = "<i h h H h"
    POINT_SIZE = struct.calcsize(POINT_STRUCT)
    points = []
    offset = 0
    while offset + POINT_SIZE <= len(payload):
        rng_cm, azi, ele, snr, dop = struct.unpack_from(
            POINT_STRUCT, payload, offset)
        points.append({
            "range_cm": rng_cm,
            "azimuth_001deg": azi,
            "elevation_001deg": ele,
            "snr_001db": snr,
            "doppler_001ms": dop,
        })
        offset += POINT_SIZE
    return {"type": "C3 Points", "count": len(points), "points": points[:20]}


def parse_c6_psic(payload: bytes) -> dict:
    """解析 C6 PSIC Debug 数据"""
    return {"type": "C6 PSIC Debug", "size": len(payload)}


# ====================================================================
#  回调
# ====================================================================

PARSERS = {
    MSGID_C1_FFT:    parse_c1_fft,
    MSGID_C2_FFT:    parse_c2_fft,
    MSGID_C3_POINTS: parse_c3_points,
    MSGID_C6_PSIC:   parse_c6_psic,
}


def on_frame(msg_id: int, payload: bytes):
    """根据 msg_id 自动选择解析器"""
    parser = PARSERS.get(msg_id)
    if parser:
        result = parser(payload)
        print(f"\n[{result['type']}] {result}")
    else:
        print(f"\n[Unknown 0x{msg_id:02X}] {len(payload)} bytes")


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else "/dev/spidev0.0"
    speed  = int(sys.argv[2]) if len(sys.argv) > 2 else 5_000_000

    rx = RadarReceiver(
        spi_device=device,
        speed_hz=speed,
        notify_chip="/dev/gpiochip0",
        notify_line=6,
    )
    rx.on_frame = on_frame

    if not rx.start():
        print(f"ERROR: Failed to open {device}")
        sys.exit(1)

    print(f"Listening on {device}... Press Ctrl+C to stop.")
    try:
        while rx.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        rx.stop()


if __name__ == "__main__":
    main()
