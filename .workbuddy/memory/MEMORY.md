# Project Memory — Possumic RS6x/7x Host Driver

## 项目概述
STM32MP157 Linux 上使用 Python 实现 RS6x/7x 毫米波雷达的 HIF Host 驱动，SPI 模式。

## 参考文档
- RS6x_7x_HIF_参考手册_V1.0 (2026-06-30), SDK V2.1.0
- Device → Possumic PSIC 雷达 SoC (RS6130/RS6240)
- 使用 SPI0: Device PA0(CS)/PA1(CLK)/PA2(MOSI)/PA3(MISO), Host Master
- SPI 速率建议: 5MHz

## 架构分层 (v2: 对照官方 SDK 7层重构)

对照官方 SDK (host_driver/) 逐层复刻:

```
API 层 (api/)
├── driver.py    — Host_Driver_Init/Deinit, Device_Regist/Open/Close
├── commands.py  — MmwCmd_* TLV 命令构造器
└── burn.py      — Burn/OTA (TODO)

HIF 层 (hif/)
├── types.py     — HIF 协议类型 (HifCfg, MsgID, Status)
├── frame.py     — Frame 编解码 + Check8/Check32
├── tl.py        — Transport Layer: 双线程 (transport_task + proc_task)
├── hif.py       — HIF 层: 命令/响应同步, 报告回调, 分片重组
└── reports.py   — C1/C2/C3/C6 数据解析

LLC 层 (llc/)
└── llc.py       — 设备生命周期, 收发+总线占用, IO控制, 通知回调

COM Bus/Device 层 (bus/)
├── bus.py       — 总线树 (SPI/I2C/UART), 使用权信号量
└── device.py    — 设备注册, DevHw 硬件配置, 虚拟ID生成

HAL 层 (hal/)
├── types.py     — 基础类型 + 错误码 + IO/COM 枚举
├── os.py        — 线程/信号量/临界区/延时 (threading)
├── io.py        — GPIO (Linux /sys/class/gpio)
├── com.py       — SPI/I2C/UART ComOps 工厂
├── log.py       — 分级日志
└── store.py     — 存储抽象

config.py        — 全局配置 (对应 host_config.h)
main.py          — 示例入口
```

## 关键协议细节
- HIF Frame: Magic(0xA5, 1B) + Check8(1B) + MsgHeader(4B) + Payload(0~4095B) + CheckSum(4B)
- SPI Wake: Host→0x55 0xFF 0x55 0xFF, Device→0x79 0x79 0x79 0x79
- Connect: MsgID=0x05, Payload=0x01
- Command-Response: MsgID=0x00~0x60, Poll=0x0C
- Report: 0xC1(C1 FFT), 0xC2(C2 FFT), 0xC3(Points), 0xC6(PSIC Debug)
- Check32 = ~sum32(MsgHeader + Payload) DWORD aligned
- SPI POLL 模式: Host 发 Dummy 字节读取 Device 数据
