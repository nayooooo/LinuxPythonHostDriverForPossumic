#!/usr/bin/env python3
"""
Possumic RS6x/7x HIF Python Host Driver — 示例程序

架构: HAL → COM Bus → COM Device → LLC → TL → HIF → API

用法:
  python3 main.py                  # 默认 /dev/spidev0.0
  python3 main.py --spi /dev/spidev1.0 --speed 8000000
"""

import sys
import time
import signal
import logging
import argparse

# ── 配置日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ── 导入所有层 ────────────────────────────────────────────────
from bus import DevHw, DevParam
from hal.types import ComType, UploadType, NotifyType
from hif import HifCfg, ReportMsgID, MsgReportCB
from api import (
    init as driver_init,
    deinit as driver_deinit,
    device_regist, device_open, device_close, device_unregist,
    general_command, version_get,
    report_regist,
    DevLocal,
    radar_start, radar_stop,
    radar_range, radar_veloc, radar_frame,
    radar_report, radar_interval,
)


# ── Report 回调 ───────────────────────────────────────────────
def on_c1_fft(msg_id: int, payload: bytes, length: int, arg):
    logger.info(f"[C1 FFT] len={length}")

def on_c2_fft(msg_id: int, payload: bytes, length: int, arg):
    logger.info(f"[C2 FFT] len={length}")

def on_c3_points(msg_id: int, payload: bytes, length: int, arg):
    from hif.reports import parse_report
    report = parse_report(msg_id, payload)
    pts = report.get("points", [])
    if pts:
        logger.info(f"[C3 Points] frame={report.get('frame_index')}, count={len(pts)}")
        for pt in pts[:3]:
            logger.info(f"  → range={pt['range_cm']}cm, "
                        f"azimuth={pt['azimuth_001deg'] * 0.01:.1f}°")

def on_c6_psic(msg_id: int, payload: bytes, length: int, arg):
    logger.info(f"[C6 PSIC] len={length}")


# ── Main ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="RS6x/7x HIF Host Driver")
    parser.add_argument("--spi", default="/dev/spidev0.0",
                        help="SPI device")
    parser.add_argument("--speed", type=int, default=5_000_000,
                        help="SPI speed Hz")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # 1. 驱动初始化
    driver_init()

    # 2. 硬件配置
    hw = DevHw(
        bus_type=ComType.SPI,
        bus_id=0,
        bus_speed=args.speed,
        bus_param=1,               # 1线 SPI
        notify_io=6,               # PA6
        upload_type=UploadType.PASSIVE,
        notify_type=NotifyType.EDGE,
        param=DevParam(cs_pin=0),
    )

    # 3. HIF 配置
    hif_cfg = HifCfg(
        poll_enable=True,
        fragment_enable=True,
        cmd_buf_len=512,
        poll_buf_len=512,
    )

    # 4. 注册设备
    local = device_regist(hw, hif_cfg)
    if local is None:
        logger.error("Device registration failed")
        return 1

    # 5. 注册 Report 回调
    report_regist(local, ReportMsgID.C1_FFT, on_c1_fft)
    report_regist(local, ReportMsgID.C2_FFT, on_c2_fft)
    report_regist(local, ReportMsgID.C3_POINTS, on_c3_points)
    report_regist(local, ReportMsgID.C6_PSIC, on_c6_psic)

    # 6. 打开设备 (LLC + HIF 初始化)
    if device_open(local) != 0:
        logger.error("Device open failed")
        return 1

    # 7. SPI 唤醒
    logger.info("Waking device...")
    if not local.hif_hdl.wake_spi():
        logger.error("SPI wake failed")
        return 1

    # 8. 连接
    logger.info("Connecting...")
    status, resp = general_command(local, 0x05, bytes([0x01]))
    logger.info(f"Connect: status={status}")

    # 9. 获取版本
    status, ver = version_get(local)
    logger.info(f"Version: status={status}, data={ver.hex() if ver else 'none'}")

    # 10. 配置雷达
    logger.info("Configuring radar...")
    for cmd in [
        radar_range(10.0, 0.1),
        radar_veloc(30.0, 0.5),
        radar_frame(64, 1, 4),
        radar_interval(50),
        radar_report(c3=True),
    ]:
        status, _ = general_command(local, cmd[0], cmd[1])
        logger.info(f"  Cfg 0x{cmd[0]:02X}: status={status}")

    # 11. 启动雷达
    logger.info("Starting radar...")
    status, _ = general_command(local, *radar_start())

    # 12. 主循环 (HIF TL 双线程已在后台运行, 负责收数据 + 回调)
    running = True

    def handler(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    logger.info("Running... Press Ctrl+C to stop")
    while running:
        time.sleep(1)

    # 13. 清理
    logger.info("Stopping radar...")
    general_command(local, *radar_stop())

    logger.info("Closing device...")
    device_close(local)
    device_unregist(local)
    driver_deinit()

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
