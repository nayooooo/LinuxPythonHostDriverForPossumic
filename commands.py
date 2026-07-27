"""
雷达配置命令构造器

对应手册第5章的 MmwCmd_* 命令,
每个函数返回 (msg_id, payload) 用于 HifDevice.send_command()
"""

import struct
from hif import CmdMsgID


# ── 通用配置命令 (通过 Generic Command 发送) ────────────────────────
# 这些命令通过 MsgID=0x07 发送, Payload 中包含子命令ID和参数

# 子命令 ID (推测, 需对照 SDK)
SUBCMD_GENERAL_START_CTRL      = 0x01
SUBCMD_GENERAL_MIMO_MODE       = 0x02
SUBCMD_GENERAL_FRAME_TYPE      = 0x03
SUBCMD_GENERAL_START_FREQ      = 0x04
SUBCMD_GENERAL_TRIGGER_RANGE   = 0x05
SUBCMD_GENERAL_RANGE_RESOLUTION = 0x06
SUBCMD_GENERAL_MAX_VELOCITY    = 0x07
SUBCMD_GENERAL_VEL_RESOLUTION  = 0x08
SUBCMD_GENERAL_FRAME_PERIOD    = 0x09


def build_general_command(subcmd_id: int, params: bytes) -> tuple[int, bytes]:
    """构建通用配置命令

    Returns:
        (msg_id=0x07, payload=subcmd_id(1B) + params)
    """
    return (0x07, bytes([subcmd_id]) + params)


def start_ctrl_cfg(enable: bool) -> tuple[int, bytes]:
    """雷达启停控制"""
    return build_general_command(SUBCMD_GENERAL_START_CTRL,
                                 struct.pack("<B", 1 if enable else 0))


def mimo_mode_cfg(mode: int) -> tuple[int, bytes]:
    """MIMO 模式配置"""
    return build_general_command(SUBCMD_GENERAL_MIMO_MODE,
                                 struct.pack("<I", mode))


def frame_type_cfg(frame_type: int) -> tuple[int, bytes]:
    """帧类型配置"""
    return build_general_command(SUBCMD_GENERAL_FRAME_TYPE,
                                 struct.pack("<I", frame_type))


def start_freq_cfg(freq_hz: int) -> tuple[int, bytes]:
    """起始频率配置 (Hz)"""
    return build_general_command(SUBCMD_GENERAL_START_FREQ,
                                 struct.pack("<I", freq_hz))


def trigger_range_cfg(range_m: float) -> tuple[int, bytes]:
    """触发量程配置 (米)"""
    # 通常用定点数: range_m * 100 (厘米) 或直接传浮点
    return build_general_command(SUBCMD_GENERAL_TRIGGER_RANGE,
                                 struct.pack("<f", range_m))


def range_resolution_cfg(resolution_m: float) -> tuple[int, bytes]:
    """距离分辨率配置 (米)"""
    return build_general_command(SUBCMD_GENERAL_RANGE_RESOLUTION,
                                 struct.pack("<f", resolution_m))


def max_velocity_cfg(velocity_mps: float) -> tuple[int, bytes]:
    """最大速度配置 (m/s)"""
    return build_general_command(SUBCMD_GENERAL_MAX_VELOCITY,
                                 struct.pack("<f", velocity_mps))


def vel_resolution_cfg(resolution_mps: float) -> tuple[int, bytes]:
    """速度分辨率配置 (m/s)"""
    return build_general_command(SUBCMD_GENERAL_VEL_RESOLUTION,
                                 struct.pack("<f", resolution_mps))


def frame_period_cfg(period_ms: int) -> tuple[int, bytes]:
    """帧周期配置 (ms)"""
    return build_general_command(SUBCMD_GENERAL_FRAME_PERIOD,
                                 struct.pack("<I", period_ms))


# ── Radar Analysis 命令 ────────────────────────────────────────────

def radar_analysis_start() -> tuple[int, bytes]:
    """启动雷达分析"""
    return (0x04, b"")


def radar_analysis_stop() -> tuple[int, bytes]:
    """停止雷达分析"""
    return (0x04, bytes([0x00]))  # 具体看手册定义


def radar_analysis_mode_cfg(mode: int) -> tuple[int, bytes]:
    """雷达分析模式配置"""
    return build_general_command(0x10, struct.pack("<I", mode))


def radar_analysis_freq_cfg(start_freq_hz: int, chirp_slope: int) -> tuple[int, bytes]:
    """频率配置"""
    return build_general_command(0x11,
                                 struct.pack("<II", start_freq_hz, chirp_slope))


def radar_analysis_range_cfg(max_range_m: float, resolution_m: float) -> tuple[int, bytes]:
    """距离配置"""
    return build_general_command(0x12,
                                 struct.pack("<ff", max_range_m, resolution_m))


def radar_analysis_veloc_cfg(max_vel_mps: float, resolution_mps: float) -> tuple[int, bytes]:
    """速度配置"""
    return build_general_command(0x13,
                                 struct.pack("<ff", max_vel_mps, resolution_mps))


def radar_analysis_frame_cfg(chirps_per_frame: int, tx_antennas: int, rx_antennas: int) -> tuple[int, bytes]:
    """帧配置"""
    return build_general_command(0x14,
                                 struct.pack("<BBB", chirps_per_frame, tx_antennas, rx_antennas))


def radar_analysis_intv_cfg(frame_period_ms: int) -> tuple[int, bytes]:
    """帧间隔配置"""
    return build_general_command(0x15,
                                 struct.pack("<I", frame_period_ms))


def radar_analysis_report_cfg(enable_c1: bool, enable_c2: bool,
                               enable_c3: bool, enable_c6: bool) -> tuple[int, bytes]:
    """Report 输出配置"""
    flags = ((1 if enable_c1 else 0) |
             ((1 if enable_c2 else 0) << 1) |
             ((1 if enable_c3 else 0) << 2) |
             ((1 if enable_c6 else 0) << 3))
    return build_general_command(0x17, struct.pack("<I", flags))
