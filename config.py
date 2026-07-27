"""
Host Driver 全局配置 (对应官方 host_config.h)

配置分为三级:
  Top-Level  — 平台与功能开关
  Protocol   — 协议层参数
  App        — 应用层行为
"""

# ═══════════════════════════════════════════════════════════════
# Top-Level: 平台与功能开关
# ═══════════════════════════════════════════════════════════════

CFG_HOST_PORT_LOG_EN              = True       # 日志使能
CFG_HOST_PORT_LOG_FILE            = None       # 日志文件路径 (None=stdout)
CFG_HOST_LOG_LEVEL                = "INFO"     # DEBUG/INFO/WARNING/ERROR

CFG_HOST_PORT_OS_EN               = True       # OS 抽象使能 (Python 必须)
CFG_HOST_PORT_IO_EN               = True       # IO 抽象使能
CFG_HOST_PORT_COM_EN              = True       # COM 通信使能
CFG_HOST_PORT_COM_SPI_EN          = True       # SPI 总线使能
CFG_HOST_PORT_COM_I2C_EN          = False      # I2C 总线使能 (暂不做)
CFG_HOST_PORT_COM_UART_EN         = False      # UART 总线使能 (暂不做)
CFG_HOST_PORT_STORE_EN            = False      # 存储抽象使能

CFG_HOST_PORT_PM_EN               = False      # 电源管理使能
CFG_HOST_DRIVER_SPI_SPEED_DW      = 8_000_000  # 下载时 SPI 速度 (8MHz)

# 线程优先级
THREAD_PRIO_LOW                   = 0
THREAD_PRIO_NORMAL                = 5          # 报告处理线程
THREAD_PRIO_HIGH                  = 10         # 传输任务线程 = NORMAL + PRIO_LOW

# 超时常量
TIMEOUT_FOREVER                   = 2**32 - 1  # 无限等待
TIMEOUT_NO_WAIT                   = 0          # 不等待
CMD_RESP_TIMEOUT_MS               = 5000       # 命令响应超时 5s

# ═══════════════════════════════════════════════════════════════
# Protocol: 协议层参数
# ═══════════════════════════════════════════════════════════════

CFG_TL_RETRY_EN                   = True       # TL 重传使能
CFG_TL_RETRY_MAX                  = 3          # 最大重传次数
CFG_TL_POLL_INTERVAL_MS           = 10         # 轮询间隔

CFG_MSG_FRAGMENT_EN               = True       # 消息分片使能
CFG_MSG_FRAGMENT_RETRY_EN         = True       # 分片重传使能
CFG_HOST_HIF_FRAGMENT_PENDING_INFO_ABNORMAL_BURST_THR = 8  # 异常分片突发阈值
CFG_HOST_HIF_FRAGMENT_FREE_INFO_NUM_MAX = 8     # 空闲分片信息池最大数
CFG_HOST_HIF_FRAGMENT_HOLD_BUFFER_SIZE_MAX = 16384  # 分片持有缓冲区最大大小

CFG_LLC_DEVICE_RX_CACHE_SIZE      = 128       # LLC 接收缓存大小
CFG_BUFFER_TIMEOUT_MS             = 2000      # 缓冲区超时

# Burn / OTA
CFG_HOST_BURN_EN                  = True       # 烧录功能使能
CFG_HOST_BURN_RETRY_CNT           = 3          # 烧录重试次数

# ═══════════════════════════════════════════════════════════════
# App: 应用层
# ═══════════════════════════════════════════════════════════════

HOST_DEVICE_STATE_INFO            = True       # 设备状态跟踪
HOST_HIF_CASE_GENERAL_EN          = True       # 通用模式使能
HOST_HIF_CASE_RADAR_ANALYSIS_EN   = True       # 雷达分析模式使能
