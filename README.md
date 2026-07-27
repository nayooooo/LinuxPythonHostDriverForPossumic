# Possumic RS6x/7x HIF Host Driver

STM32MP157 Linux 上使用 Python 实现的 RS6x/7x 毫米波雷达 HIF Host 驱动，SPI 模式。

**单文件驱动**，只需 `import host_driver` 即可使用。

---

## 目录

- [快速开始](#快速开始)
- [架构概览](#架构概览)
- [模块文档](#模块文档)
  - [HIF 协议常量](#hif-协议常量)
  - [Checksum 函数](#checksum-函数)
  - [MsgHeader — 消息头](#msgheader--消息头)
  - [HifFrame — HIF 帧](#hifframe--hif-帧)
  - [FragmentAssembler — 分片重组器](#fragmentassembler--分片重组器)
  - [SpiDev — SPI 设备](#spidev--spi-设备)
  - [RadarReceiver — 雷达接收器](#radarreceiver--雷达接收器)
- [使用示例](#使用示例)
- [Report 数据格式](#report-数据格式)

---

## 快速开始

```python
from host_driver import RadarReceiver

def on_frame(msg_id: int, payload: bytes):
    print(f"Frame 0x{msg_id:02X}: {len(payload)} bytes")

rx = RadarReceiver(
    "/dev/spidev0.0",
    speed_hz=5_000_000,
    notify_chip="/dev/gpiochip0",
    notify_line=6,               # PA6
)
rx.on_frame = on_frame
rx.start()

# ... 数据自动到达, 完整帧自动回调 ...

rx.stop()
```

或直接运行：

```bash
python host_driver.py /dev/spidev0.0 5000000 /dev/gpiochip0 6
```

---

## 架构概览

```
用户回调
    ↑
RadarReceiver  ─── 后台线程, 字节流 → 帧提取 → 分片重组 → 投递
    ├── SpiDev          ─── Linux spidev (ioctl 配置模式/速度)
    └── gpiolib.GPIO    ─── NOTIFY IO 中断 (Mode.INTERRUPT, Edge.RISING)
         ↑                        ↑
    FragmentAssembler    HifFrame / MsgHeader / check8 / check32
```

---

## 模块文档

### HIF 协议常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `HIF_MAGIC` | `0xA5` | 帧起始魔数 |
| `HIF_HEADER_SIZE` | `6` | Magic(1) + Check8(1) + MsgHeader(4) |
| `HIF_CHECKSUM_SIZE` | `4` | Check32 占用字节 |
| `HIF_MAX_PAYLOAD` | `4095` | 最大载荷长度 |
| `HIF_FRAME_MAX` | `4105` | 最大帧总长 |
| `MSGID_C1_FFT` | `0xC1` | 1D Range FFT 报告 |
| `MSGID_C2_FFT` | `0xC2` | 2D Range-Doppler FFT 报告 |
| `MSGID_C3_POINTS` | `0xC3` | 目标检测点云报告 |
| `MSGID_C6_PSIC` | `0xC6` | PSIC Debug 报告 |

---

### Checksum 函数

#### `check8(magic, msghdr) -> int`

帧头校验。对 Magic 和 MsgHeader 按字节求和取反。

```
check8 = ~sum8(Magic + MsgHeader) & 0xFF
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `magic` | `int` | Magic 字节 (0xA5) |
| `msghdr` | `bytes` | 4 字节 MsgHeader |
| 返回 | `int` | 8 位校验值 |

#### `check32(msghdr, payload) -> int`

帧校验。对 MsgHeader + Payload 按 4 字节一组累加 (little-endian uint32)，末尾不足 4 字节补 0x00 对齐。

```
check32 = ~sum32(MsgHeader + Payload) & 0xFFFFFFFF
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `msghdr` | `bytes` | 4 字节 MsgHeader |
| `payload` | `bytes` | 载荷数据 |
| 返回 | `int` | 32 位校验值 |

---

### MsgHeader — 消息头

`@dataclass` 类型，表示解析后的 HIF 消息头 4 字节。

**字节布局 (LSB)：**

| 字节 | 位域 | 说明 |
|------|------|------|
| Byte0 | `[1:0]` | `type_` — 帧类型: 0=H2H, 1=H2D, 2=D2H |
| | `[2]` | `req` — 0=Response, 1=Command |
| | `[3]` | `enc` — 加密标志 |
| | `[4]` | `has_checksum` — 是否含 Check32 |
| | `[5]` | `more` — 更多分片标志 |
| | `[6]` | `ext` — 扩展标志 |
| | `[7]` | `mac` — MAC 标志 |
| Byte1 | `[7:0]` | `msg_id` — 消息 ID (0x00~0xFF) |
| Byte2-3 | `[11:0]` | `length` — 载荷长度 (0~4095) |
| | `[14:12]` | `seq` — 流序列号 (0~7) |
| | `[15]` | `frag` — 分片标志 (0=末片, 1=更多) |

**属性：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `type_` | `int` | 帧类型 |
| `req` | `int` | 请求/响应 |
| `enc` | `int` | 加密 |
| `has_checksum` | `int` | 含 Check32 |
| `more` | `int` | 更多分片 |
| `ext` | `int` | 扩展 |
| `mac` | `int` | MAC |
| `msg_id` | `int` | 消息 ID |
| `length` | `int` | 载荷长度 |
| `seq` | `int` | 流序列号 |
| `frag` | `int` | 分片标志 |
| `is_fragment` | `bool` | 是否为分片 (frag=1 or more=1) |
| `is_last` | `bool` | 是否为末片 (frag=0 and more=0) |

#### `parse_msgheader(data) -> MsgHeader | None`

解析 4 字节 MsgHeader 原始数据。

| 参数 | 类型 | 说明 |
|------|------|------|
| `data` | `bytes` | 4 字节原始 MsgHeader |
| 返回 | `MsgHeader \| None` | 解析结果, 数据不足时返回 None |

---

### HifFrame — HIF 帧

`@dataclass` 类型，表示解析后的完整 HIF 帧。

```
┌────────┬────────┬────────────────────┬───────────┬──────────┐
│Magic   │Check8  │ MsgHeader (4B)     │ Payload   │ Check32  │
│0xA5(1B)│1B      │[Ctrl│MsgID│Len+Seq]│ 0~4095 B  │ 4B       │
└────────┴────────┴────────────────────┴───────────┴──────────┘
```

**属性：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `header` | `MsgHeader` | 消息头 |
| `payload` | `bytes` | 载荷数据 |
| `check8_ok` | `bool` | Check8 校验通过 |
| `valid` | `bool` | Check32 校验通过 (帧完整性) |
| `msg_id` | `int` | 快捷访问 `header.msg_id` |
| `payload_len` | `int` | 快捷访问 `len(payload)` |

#### `unpack_frame(data) -> HifFrame | None`

解包 HIF Frame。

| 参数 | 类型 | 说明 |
|------|------|------|
| `data` | `bytes` | 完整帧数据 (≥ Header + Payload + Check32) |
| 返回 | `HifFrame \| None` | 解析结果, Magic 不匹配/数据不完整返回 None |

校验流程：Magic(0xA5) → Check8 → Payload长度 → Check32。任一步失败返回 None。

---

### FragmentAssembler — 分片重组器

按 `msg_id` 分组累积载荷分片，收到末片后自动组装。

**构造参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `msg_id` | `int` | 必填 | 消息 ID |
| `seq` | `int` | `0` | 流序列号 |

**属性：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `msg_id` | `int` | 消息 ID |
| `parts` | `list[bytes]` | 已累积的分片列表 |
| `frag_count` | `int` | 已收到的分片数 |
| `total_len` | `int` | 已累积的总字节数 |
| `age` | `float` | 从创建到现在的秒数 |

**方法：**

| 方法 | 说明 |
|------|------|
| `add(payload)` | 添加一个分片载荷 |
| `assemble() -> bytes` | 组装所有分片为完整数据 |

---

### SpiDev — SPI 设备

Linux spidev 封装，基于 `/dev/spidevX.Y` 和 `ioctl`。

**构造参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `device` | `str` | `"/dev/spidev0.0"` | SPI 设备节点 |
| `speed_hz` | `int` | `5_000_000` | 时钟频率 (Hz) |
| `mode` | `int` | `0` | SPI 模式 (0~3) |
| `bits_per_word` | `int` | `8` | 每字位数 |

**方法：**

| 方法 | 返回 | 说明 |
|------|------|------|
| `open()` | `bool` | 打开设备并配置 (mode/speed/bits)，返回是否成功 |
| `close()` | — | 关闭设备 |
| `read(size)` | `bytes` | POLL 模式读取 MISO 数据 |
| `write(data)` | `int` | 写入 MOSI 数据，返回写入字节数 |
| `is_open` | `bool` | 设备是否已打开 |

---

### RadarReceiver — 雷达接收器

核心类。封装 SPI 读取、帧解析、分片重组、回调投递全流程。

**类型别名：**

```python
FrameCallback = Callable[[int, bytes], None]
# callback(msg_id: int, payload: bytes)
```

**构造参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `spi_device` | `str` | `"/dev/spidev0.0"` | SPI 设备节点 |
| `speed_hz` | `int` | `5_000_000` | SPI 时钟 (Hz) |
| `spi_mode` | `int` | `0` | SPI 模式 |
| `notify_chip` | `str \| None` | `None` | NOTIFY IO GPIO 芯片路径，None 则纯轮询 |
| `notify_line` | `int \| None` | `None` | NOTIFY IO GPIO 引脚编号 |
| `notify_edge` | `str` | `"rising"` | 边沿: `"rising"` / `"falling"` / `"both"` |
| `read_size` | `int` | `4096` | 每次 SPI 读取字节数 |
| `frag_timeout` | `float` | `5.0` | 分片重组超时 (秒) |
| `log_level` | `int` | `logging.INFO` | 日志级别 |

**公开属性：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `on_frame` | `FrameCallback \| None` | 帧回调 (可读写) |
| `is_running` | `bool` | 是否正在接收 (只读) |
| `stats` | `dict` | 统计信息 (只读) |

**`stats` 字典字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `frames` | `int` | 已接收完整帧数 |
| `fragments` | `int` | 已处理分片数 |
| `errors` | `int` | 错误帧数 (校验失败) |
| `bytes` | `int` | 已读取字节总数 |
| `pending_frags` | `int` | 待完成的分片重组数 |

**公开方法：**

| 方法 | 返回 | 说明 |
|------|------|------|
| `start()` | `bool` | 启动接收。打开 SPI + NOTIFY IO + 后台线程。返回是否成功 |
| `stop()` | — | 停止接收。关闭线程 + GPIO + SPI，打印统计 |

**工作模式：**

| 模式 | 条件 | 行为 |
|------|------|------|
| 中断模式 | gpiolib 已安装 + notify 参数已配置 | `gpio.check(timeout=0.1)` 阻塞等待上升沿，触发后读 SPI |
| 轮询模式 | gpiolib 不可用 / 未配置 NOTIFY IO | 1ms 间隔持续读 SPI，自动降级无感切换 |

**内部方法 (不直接调用)：**

| 方法 | 说明 |
|------|------|
| `_recv_loop()` | 后台接收线程主循环 |
| `_drain_frames(buf)` | 从字节流提取 HIF Frame |
| `_handle_frame(frame)` | 帧分发 (分片/完整) |
| `_accumulate_fragment(...)` | 分片累积与重组 |
| `_deliver(msg_id, payload)` | 投递到用户回调 |
| `_cleanup_stale()` | 清理超时分片 (每秒) |

---

## 使用示例

### 基础接收

```python
from host_driver import RadarReceiver

def on_frame(msg_id, payload):
    print(f"Frame 0x{msg_id:02X}: {len(payload)} bytes")

rx = RadarReceiver("/dev/spidev0.0")
rx.on_frame = on_frame
rx.start()

import time
time.sleep(60)   # 接收 60 秒
rx.stop()
```

### 带 NOTIFY IO

```python
rx = RadarReceiver(
    "/dev/spidev0.0",
    notify_chip="/dev/gpiochip0",
    notify_line=6,         # PA6
    notify_edge="rising",
)
```

### 查看统计

```python
import time
while rx.is_running:
    time.sleep(5)
    print(rx.stats)
    # {"frames": 1234, "fragments": 0, "errors": 2, "bytes": 512000, "pending_frags": 0}
```

### 解析 Report 数据

```python
from host_driver import RadarReceiver, MSGID_C1_FFT, MSGID_C3_POINTS
import struct

def on_frame(msg_id, payload):
    if msg_id == MSGID_C1_FFT:
        # C1: 1D Range FFT
        frame_idx, frame_len, offset = struct.unpack_from("<HHH", payload, 0)
        print(f"Range FFT: frame={frame_idx}, bins={len(payload) // 4}")

    elif msg_id == MSGID_C3_POINTS:
        # C3: 目标点云
        POINT_SIZE = 16
        n = len(payload) // POINT_SIZE
        print(f"Points: count={n}")
```

更多示例见 `example/` 目录。

---

## Report 数据格式

### C1 — 1D Range FFT

| 偏移 | 大小 | 类型 | 字段 |
|------|------|------|------|
| 0 | 2 | uint16 LE | `frame_index` |
| 2 | 2 | uint16 LE | `frame_len` |
| 4 | 2 | uint16 LE | `data_offset` |
| 6 | N×4 | int16×2 LE | `bins[]` — 每个 bin 为 (real, imag) |

### C2 — 2D Range-Doppler FFT

| 偏移 | 大小 | 类型 | 字段 |
|------|------|------|------|
| 0 | 1 | uint8 | `tx` — 发射天线数 |
| 1 | 1 | uint8 | `rx` — 接收天线数 |
| 2 | 1 | uint8 | `range_bins` — 距离 bin 数 |
| 3 | 1 | uint8 | `dop_bins` — 多普勒 bin 数 |
| 4 | range_bins×dop_bins×4 | float LE | FFT 数据矩阵 |

### C3 — 目标检测点云

每点典型 16 字节：

| 偏移 | 大小 | 类型 | 字段 |
|------|------|------|------|
| 0 | 4 | int32 LE | `range_cm` — 距离 (cm) |
| 4 | 2 | int16 LE | `azimuth_001deg` — 方位角 (×0.001°) |
| 6 | 2 | int16 LE | `elevation_001deg` — 俯仰角 (×0.001°) |
| 8 | 2 | uint16 LE | `snr_001db` — 信噪比 (×0.001 dB) |
| 10 | 2 | int16 LE | `doppler_001ms` — 多普勒速度 (×0.001 m/s) |
| 12 | 4 | — | 保留/扩展 |

### C6 — PSIC Debug

内部调试数据，格式随设备配置变化。

---

## 依赖

- **Linux**: `/dev/spidevX.Y`, `fcntl`
- **gpiolib** (可选): NOTIFY IO 中断模式，基于 `python-periphery`
- **Python**: ≥ 3.7, 标准库仅 `os/struct/time/threading/logging`

---

## 文件结构

```
.
├── host_driver.py          ← 核心驱动 (单文件)
├── example/
│   ├── basic_receive.py    ← 基础接收示例
│   └── parse_reports.py    ← Report 解析示例
└── README.md               ← 本文档
```
