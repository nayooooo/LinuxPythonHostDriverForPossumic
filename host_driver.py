"""
HIF Host Driver — 顶层驱动

提供回调式 Report 处理 + 配置接口,
对应手册第5章 Host_Driver_* 和 Mmw_* API

架构:
  ┌──────────────────────────────────────────────────┐
  │                  HostDriver                       │
  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  │
  │  │ C1 Handler │  │ C2 Handler │  │ C3 Handler │  │
  │  └────────────┘  └────────────┘  └────────────┘  │
  │  ┌────────────────────────────────────────────┐  │
  │  │            Poll Thread (轮询线程)            │  │
  │  │  ┌──────────────────────────────────────┐  │  │
  │  │  │  loop: poll→parse_report→callback    │  │  │
  │  │  └──────────────────────────────────────┘  │  │
  │  └────────────────────────────────────────────┘  │
  └──────────────────────────────────────────────────┘
"""

import threading
import time
import logging
import traceback
from typing import Callable

from device import HifDevice
from transport import HifTransport, SpiTransport
from hif import ReportMsgID
from hif.reports import parse_report

logger = logging.getLogger(__name__)


# ── Report Callback 类型 ──────────────────────────────────────────
ReportCallback = Callable[[int, dict], None]
"""
Report 回调: callback(msg_id, report_data)
  msg_id: Report MsgID (0xC1/0xC2/0xC3/0xC6)
  report_data: parse_report 返回的 dict
"""


class HostDriver:
    """HIF Host 驱动

    用法:
        transport = SpiTransport("/dev/spidev0.0")
        driver = HostDriver(transport)
        driver.register_callback(ReportMsgID.C3_POINTS, on_points)

        driver.open()
        driver.wake()
        driver.connect()
        driver.start_polling()

        # 配置雷达
        driver.configure_range(max_range=10.0, resolution=0.1)
        driver.start_radar()

        # ... 运行 ...
        driver.stop_radar()
        driver.close()
    """

    def __init__(self, transport: HifTransport):
        self._device = HifDevice(transport)
        self._callbacks: dict[int, list[ReportCallback]] = {
            ReportMsgID.C1_FFT: [],
            ReportMsgID.C2_FFT: [],
            ReportMsgID.C3_POINTS: [],
            ReportMsgID.C6_PSIC: [],
        }
        self._poll_thread: threading.Thread | None = None
        self._poll_running = False
        self._poll_interval_ms = 10

    # ── Lifecycle ───────────────────────────────────────────────

    def open(self) -> bool:
        """打开传输通道"""
        return self._device._transport.open()

    def close(self):
        """关闭驱动"""
        self.stop_polling()
        self._device.close()
        logger.info("HostDriver closed")

    def wake(self) -> bool:
        """唤醒设备 (SPI wake sequence)"""
        return self._device.wake()

    def connect(self) -> bool:
        """连接设备"""
        return self._device.connect()

    def is_active(self) -> bool:
        return self._device.is_active()

    # ── Report Callbacks ────────────────────────────────────────

    def register_callback(self, msg_id: int, callback: ReportCallback):
        """注册 Report 回调

        Args:
            msg_id: Report MsgID (0xC1/0xC2/0xC3/0xC6)
            callback: 回调函数 callback(msg_id, report_data)
        """
        if msg_id in self._callbacks:
            self._callbacks[msg_id].append(callback)
            logger.info(f"Registered callback for 0x{msg_id:02X}")
        else:
            logger.warning(f"Unknown report MsgID: 0x{msg_id:02X}")

    def unregister_callback(self, msg_id: int, callback: ReportCallback):
        """注销回调"""
        if msg_id in self._callbacks and callback in self._callbacks[msg_id]:
            self._callbacks[msg_id].remove(callback)

    def clear_callbacks(self, msg_id: int | None = None):
        """清除回调"""
        if msg_id is not None:
            if msg_id in self._callbacks:
                self._callbacks[msg_id].clear()
        else:
            for k in self._callbacks:
                self._callbacks[k].clear()

    # ── Polling Thread ──────────────────────────────────────────

    def start_polling(self, interval_ms: int = 10):
        """启动轮询线程

        周期性发送 Poll 命令获取 Device 的 Report 数据
        """
        if self._poll_running:
            logger.warning("Polling already running")
            return

        self._poll_interval_ms = interval_ms
        self._poll_running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="HIF-Poll"
        )
        self._poll_thread.start()
        logger.info(f"Poll thread started (interval={interval_ms}ms)")

    def stop_polling(self):
        """停止轮询线程"""
        self._poll_running = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        logger.info("Poll thread stopped")

    def _poll_loop(self):
        """轮询循环 (运行在独立线程)"""
        logger.debug("Poll loop started")
        while self._poll_running:
            try:
                report = self._device.poll_report()
                if report and "error" not in report:
                    # 分发到对应回调
                    msg_id_str = report.get("msg_type", "")
                    self._dispatch_report(report)
            except Exception as e:
                if self._poll_running:
                    logger.error(f"Poll error: {e}")
                    traceback.print_exc()

            time.sleep(self._poll_interval_ms / 1000.0)
        logger.debug("Poll loop exited")

    def _dispatch_report(self, report: dict):
        """根据报告类型分发到注册的回调"""
        msg_type = report.get("msg_type", "")

        # 映射 msg_type 字符串到 MsgID
        type_to_id = {
            "C1_FFT":   ReportMsgID.C1_FFT,
            "C2_FFT":   ReportMsgID.C2_FFT,
            "C3_POINTS": ReportMsgID.C3_POINTS,
            "C6_PSIC":  ReportMsgID.C6_PSIC,
        }
        msg_id = type_to_id.get(msg_type, -1)

        if msg_id in self._callbacks:
            for cb in self._callbacks[msg_id]:
                try:
                    cb(msg_id, report)
                except Exception as e:
                    logger.error(f"Callback error for 0x{msg_id:02X}: {e}")

    # ── Send Command ────────────────────────────────────────────

    def send_command(self, msg_id: int, payload: bytes = b"",
                     timeout_ms: int = 500) -> dict | None:
        """发送命令 (转发到 HifDevice)"""
        return self._device.send_command(msg_id, payload, timeout_ms)

    def send_command_ex(self, cmd: tuple[int, bytes],
                        timeout_ms: int = 500) -> dict | None:
        """发送命令 (接受 (msg_id, payload) 元组)"""
        return self._device.send_command(cmd[0], cmd[1], timeout_ms)

    # ── Radar Config Convenience ────────────────────────────────

    def get_version(self) -> dict | None:
        return self._device.get_version()

    def get_sample_id(self) -> dict | None:
        return self._device.get_sample_id()

    def configure_range(self, max_range: float, resolution: float) -> dict | None:
        """配置测距参数"""
        from commands import radar_analysis_range_cfg
        return self.send_command_ex(radar_analysis_range_cfg(max_range, resolution))

    def configure_velocity(self, max_vel: float, resolution: float) -> dict | None:
        """配置测速参数"""
        from commands import radar_analysis_veloc_cfg
        return self.send_command_ex(radar_analysis_veloc_cfg(max_vel, resolution))

    def configure_frame(self, chirps: int, tx: int, rx: int) -> dict | None:
        """配置帧参数"""
        from commands import radar_analysis_frame_cfg
        return self.send_command_ex(radar_analysis_frame_cfg(chirps, tx, rx))

    def configure_report(self, c1: bool = False, c2: bool = False,
                         c3: bool = True, c6: bool = False) -> dict | None:
        """配置 Report 输出类型"""
        from commands import radar_analysis_report_cfg
        return self.send_command_ex(radar_analysis_report_cfg(c1, c2, c3, c6))

    def set_frame_period(self, period_ms: int) -> dict | None:
        """设置帧周期"""
        from commands import radar_analysis_intv_cfg
        return self.send_command_ex(radar_analysis_intv_cfg(period_ms))

    def start_radar(self) -> dict | None:
        """启动雷达分析"""
        from commands import radar_analysis_start
        return self.send_command_ex(radar_analysis_start())

    def stop_radar(self) -> dict | None:
        """停止雷达分析"""
        from commands import radar_analysis_stop
        return self.send_command_ex(radar_analysis_stop())
