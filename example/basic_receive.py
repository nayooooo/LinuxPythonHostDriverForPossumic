#!/usr/bin/env python3
"""
基础接收示例 — 注册回调, 接收完整帧数据

用法 (开发板):
    python example/basic_receive.py /dev/spidev0.0
"""

import sys
import os
import time

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from host_driver import RadarReceiver


def on_frame(msg_id: int, payload: bytes):
    """每收到一个完整帧 (分片已自动重组) 调用一次"""
    print(f"[Frame] MsgID=0x{msg_id:02X}, size={len(payload)} bytes")


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
            s = rx.stats
            print(f"  Stats: frames={s['frames']}, frags={s['fragments']}, "
                  f"errors={s['errors']}, bytes={s['bytes']}")
    except KeyboardInterrupt:
        pass
    finally:
        rx.stop()
        print("Stopped.")


if __name__ == "__main__":
    main()
