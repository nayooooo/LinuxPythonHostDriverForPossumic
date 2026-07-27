"""
API 层 — mmWave 命令构造器 (对应 host_mmw_api.h + host_mmw_cmd_api.c)

基于 TLV 格式: [type:1B][len:1B][value:len_B]
"""

import struct

# ── 子命令 ID ──────────────────────────────────────────────────
SUBCMD_START_CTRL      = 0xA0
SUBCMD_MIMO_MODE       = 0xA1
SUBCMD_FRAME_TYPE      = 0xA2
SUBCMD_START_FREQ      = 0xA3
SUBCMD_TRIGGER_RANGE   = 0xA4
SUBCMD_RANGE_RES       = 0xA5
SUBCMD_MAX_VELOCITY    = 0xA6
SUBCMD_VEL_RES         = 0xA7
SUBCMD_FRAME_PERIOD    = 0xA8

SUBCMD_RADAR_START     = 0xB0
SUBCMD_RADAR_STOP      = 0xB1
SUBCMD_RADAR_MODE      = 0xB2
SUBCMD_RADAR_FREQ      = 0xB3
SUBCMD_RADAR_RANGE     = 0xB4
SUBCMD_RADAR_VELOC     = 0xB5
SUBCMD_RADAR_FRAME     = 0xB6
SUBCMD_RADAR_INTV      = 0xB7
SUBCMD_RADAR_CHIRP_NUM = 0xB8
SUBCMD_RADAR_REPORT    = 0xB9
SUBCMD_RADAR_DOP_FFT   = 0xBA


def _tlv(type_id: int, value: bytes) -> bytes:
    """构建 TLV: type(1B) + len(1B) + value"""
    return struct.pack("<BB", type_id, len(value)) + value


def _tlv_u8(type_id: int, val: int) -> bytes:
    return _tlv(type_id, struct.pack("<B", val))


def _tlv_u32(type_id: int, val: int) -> bytes:
    return _tlv(type_id, struct.pack("<I", val))


def _tlv_f32(type_id: int, val: float) -> bytes:
    return _tlv(type_id, struct.pack("<f", val))


# ── General Commands ────────────────────────────────────────────

def start_ctrl(enable: bool) -> tuple[int, bytes]:
    """启停控制"""
    return (0x07, _tlv_u8(SUBCMD_START_CTRL, 1 if enable else 0))


def mimo_mode(mode: int) -> tuple[int, bytes]:
    """MIMO 模式"""
    return (0x07, _tlv_u32(SUBCMD_MIMO_MODE, mode))


def frame_type(ftype: int) -> tuple[int, bytes]:
    """帧类型"""
    return (0x07, _tlv_u32(SUBCMD_FRAME_TYPE, ftype))


def start_freq(freq_hz: int) -> tuple[int, bytes]:
    """起始频率 (Hz)"""
    return (0x07, _tlv_u32(SUBCMD_START_FREQ, freq_hz))


def trigger_range(range_m: float) -> tuple[int, bytes]:
    """触发量程 (m)"""
    return (0x07, _tlv_f32(SUBCMD_TRIGGER_RANGE, range_m))


def range_resolution(res_m: float) -> tuple[int, bytes]:
    """距离分辨率 (m)"""
    return (0x07, _tlv_f32(SUBCMD_RANGE_RES, res_m))


def max_velocity(vel_mps: float) -> tuple[int, bytes]:
    """最大速度 (m/s)"""
    return (0x07, _tlv_f32(SUBCMD_MAX_VELOCITY, vel_mps))


def vel_resolution(res_mps: float) -> tuple[int, bytes]:
    """速度分辨率 (m/s)"""
    return (0x07, _tlv_f32(SUBCMD_VEL_RES, res_mps))


def frame_period(period_ms: int) -> tuple[int, bytes]:
    """帧周期 (ms)"""
    return (0x07, _tlv_u32(SUBCMD_FRAME_PERIOD, period_ms))


# ── Radar Analysis Commands ─────────────────────────────────────

def radar_start() -> tuple[int, bytes]:
    """启动雷达分析"""
    return (0x07, _tlv_u8(SUBCMD_RADAR_START, 1))


def radar_stop() -> tuple[int, bytes]:
    """停止雷达分析"""
    return (0x07, _tlv_u8(SUBCMD_RADAR_STOP, 0))


def radar_mode(mode: int) -> tuple[int, bytes]:
    """雷达分析模式"""
    return (0x07, _tlv_u32(SUBCMD_RADAR_MODE, mode))


def radar_freq(start_hz: int, slope: int) -> tuple[int, bytes]:
    """频率配置"""
    return (0x07, _tlv(type_id=SUBCMD_RADAR_FREQ,
                       value=struct.pack("<II", start_hz, slope)))


def radar_range(max_m: float, res_m: float) -> tuple[int, bytes]:
    """距离配置"""
    return (0x07, _tlv(type_id=SUBCMD_RADAR_RANGE,
                       value=struct.pack("<ff", max_m, res_m)))


def radar_veloc(max_mps: float, res_mps: float) -> tuple[int, bytes]:
    """速度配置"""
    return (0x07, _tlv(type_id=SUBCMD_RADAR_VELOC,
                       value=struct.pack("<ff", max_mps, res_mps)))


def radar_frame(chirps: int, tx: int, rx: int) -> tuple[int, bytes]:
    """帧配置"""
    return (0x07, _tlv(type_id=SUBCMD_RADAR_FRAME,
                       value=struct.pack("<BBB", chirps, tx, rx)))


def radar_interval(period_ms: int) -> tuple[int, bytes]:
    """帧间隔"""
    return (0x07, _tlv_u32(SUBCMD_RADAR_INTV, period_ms))


def radar_chirp_num(num: int) -> tuple[int, bytes]:
    """Chirp 数量"""
    return (0x07, _tlv_u32(SUBCMD_RADAR_CHIRP_NUM, num))


def radar_report(c1: bool = False, c2: bool = False,
                 c3: bool = True, c6: bool = False) -> tuple[int, bytes]:
    """Report 输出配置"""
    flags = ((1 if c1 else 0) | ((1 if c2 else 0) << 1) |
             ((1 if c3 else 0) << 2) | ((1 if c6 else 0) << 3))
    return (0x07, _tlv_u32(SUBCMD_RADAR_REPORT, flags))


def radar_dop_fft(enable: bool) -> tuple[int, bytes]:
    """多普勒 FFT 使能"""
    return (0x07, _tlv_u8(SUBCMD_RADAR_DOP_FFT, 1 if enable else 0))
