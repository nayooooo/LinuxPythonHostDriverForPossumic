"""
Host Driver API 层 (对应 host_driver.h/.c)

顶层 API:
  Host_Driver_Init() / Host_Driver_Deinit()
  Host_Device_Regist() / Host_Device_Open() / Host_Device_Close()
  Host_General_Command()
  HostCmd_Version_Get()
  HostCmd_Device_Reboot()

设备管理:
  DevLocal_t 链表 → 全局 g_host_drv.pDevs
  oper_sem 互斥信号量保护链表操作
"""

import logging
import time
from typing import Optional, Callable
from dataclasses import dataclass, field

from bus import DevHw, HostBusDevice, device_register, device_unregister
from llc import LLCHandle
from hif import HIFHandle, HifCfg, MsgReportCB, CmdMsgID, CmdStatus
from hal import sem_create, sem_delete, sem_take, sem_give

logger = logging.getLogger("api.driver")

# ── 设备本地数据 (对应 DevLocal_t) ──────────────────────────────

@dataclass
class DevLocal:
    """设备本地数据"""
    next_dev: Optional["DevLocal"] = None  # 链表
    device: Optional[HostBusDevice] = None
    llc_hdl: Optional[LLCHandle] = None
    hif_hdl: Optional[HIFHandle] = None
    valid_mark: str = ""                 # 'V'=有效
    burn_mode: bool = False
    dev_state: str = "NONE"              # NONE / REGIST / OPEN
    hif_cfg: HifCfg = field(default_factory=HifCfg)
    hw: Optional[DevHw] = None


# ── 全局驱动状态 ──────────────────────────────────────────────

@dataclass
class _GlobalDriver:
    """全局驱动状态"""
    pDevs: Optional[DevLocal] = None     # 设备链表头
    oper_sem: int = 0                    # 操作互斥信号量
    init_done: bool = False

_g_hdrv = _GlobalDriver()


# ── Host_Driver_Init ────────────────────────────────────────────

def init() -> int:
    """Host_Driver_Init - 初始化驱动"""
    global _g_hdrv

    if _g_hdrv.init_done:
        return 0

    # 创建操作信号量 (计数值=1, 上限=1 → 二值信号量)
    _g_hdrv.oper_sem = sem_create(1, 1)
    _g_hdrv.init_done = True

    logger.info("Host driver initialized")
    return 0


# ── Host_Driver_Deinit ──────────────────────────────────────────

def deinit() -> int:
    """Host_Driver_Deinit - 反初始化驱动"""
    global _g_hdrv

    # 遍历设备链表, 逐个关闭
    dev = _g_hdrv.pDevs
    while dev:
        dev.valid_mark = ""

        if dev.llc_hdl:
            dev.llc_hdl.close()

        if dev.hif_hdl:
            dev.hif_hdl.deinit()

        if dev.device:
            device_unregister(dev.device)

        dev = dev.next_dev

    _g_hdrv.pDevs = None
    sem_delete(_g_hdrv.oper_sem)
    _g_hdrv.init_done = False

    logger.info("Host driver deinitialized")
    return 0


# ── Host_Device_Regist ─────────────────────────────────────────

def device_regist(hw: DevHw, hif_cfg: Optional[HifCfg] = None) -> Optional[DevLocal]:
    """Host_Device_Regist - 注册设备

    Returns:
        DevLocal (设备句柄) 或 None
    """
    global _g_hdrv

    if not _g_hdrv.init_done:
        logger.error("Driver not initialized")
        return None

    sem_take(_g_hdrv.oper_sem, 1000)

    device = None
    hif_hdl = None

    try:
        # 1. 注册到 BUS 层
        device = device_register(hw)
        if device is None:
            return None

        # 2. 创建 LLC Handle
        llc_hdl = LLCHandle(device=device, hw=hw)

        # 3. 创建 HIF Handle
        cfg = hif_cfg if hif_cfg else HifCfg()
        hif_hdl = HIFHandle(cfg=cfg, llc_hdl=llc_hdl)

        # 4. 创建设备本地数据
        local = DevLocal(
            device=device,
            llc_hdl=llc_hdl,
            hif_hdl=hif_hdl,
            valid_mark="V",
            dev_state="REGIST",
            hif_cfg=cfg,
            hw=hw,
        )

        # 5. 加入全局链表
        _put_dev(local)

        logger.info(f"Device registered: bus={hw.bus_type.name}[{hw.bus_id}]")
        return local

    except Exception as e:
        # 回滚: 清理已分配的资源
        logger.error(f"Device registration failed: {e}")
        if hif_hdl:
            hif_hdl.deinit()
        if device:
            device_unregister(device)
        return None

    finally:
        sem_give(_g_hdrv.oper_sem)


# ── Host_Device_Open ───────────────────────────────────────────

def device_open(local: DevLocal) -> int:
    """Host_Device_Open - 打开设备"""
    global _g_hdrv

    if local.valid_mark != "V":
        return -1

    sem_take(_g_hdrv.oper_sem, 1000)

    try:
        # 1. LLC 打开设备
        if local.llc_hdl:
            local.llc_hdl.open()

        # 2. HIF 初始化
        if local.hif_hdl and local.llc_hdl:
            local.hif_hdl.init(local.llc_hdl)

        local.dev_state = "OPEN"
        logger.info(f"Device opened: vid=0x{local.device.virtual_id:04X}")
        return 0
    finally:
        sem_give(_g_hdrv.oper_sem)


# ── Host_Device_Close ──────────────────────────────────────────

def device_close(local: DevLocal) -> int:
    """Host_Device_Close - 关闭设备"""
    global _g_hdrv

    sem_take(_g_hdrv.oper_sem, 1000)
    try:
        if local.llc_hdl:
            local.llc_hdl.close()

        if local.hif_hdl:
            local.hif_hdl.deinit()

        local.dev_state = "REGIST"
        return 0
    finally:
        sem_give(_g_hdrv.oper_sem)


# ── Host_Device_Unregist ───────────────────────────────────────

def device_unregist(local: DevLocal) -> int:
    global _g_hdrv

    sem_take(_g_hdrv.oper_sem, 1000)
    try:
        local.valid_mark = ""

        if local.llc_hdl:
            local.llc_hdl.close()
        if local.hif_hdl:
            local.hif_hdl.deinit()
        if local.device:
            device_unregister(local.device)

        # 从链表中移除
        _remove_dev(local)

        logger.info("Device unregistered")
        return 0
    finally:
        sem_give(_g_hdrv.oper_sem)


# ── Host_General_Command ───────────────────────────────────────

def general_command(local: DevLocal, msg_id: int, param: bytes,
                    resp_buf_len: int = 256,
                    timeout_ms: int = 5000) -> tuple[int, bytes]:
    """Host_General_Command - 发送通用命令

    Returns:
        (status: int, response_payload: bytes)
        status < 0 表示错误
    """
    if local.hif_hdl is None:
        return (-1, b"")

    resp_buf = bytearray(resp_buf_len)
    ret = local.hif_hdl.cmd_request(msg_id, param, resp_buf, resp_buf_len, timeout_ms)

    if ret < 0:
        return (ret, b"")
    return (0, bytes(resp_buf[:ret]))


# ── 版本/心跳/Reset ───────────────────────────────────────────

def version_get(local: DevLocal) -> tuple[int, bytes]:
    """获取设备版本"""
    return general_command(local, CmdMsgID.VERSION_GET, b"")


def device_tick(local: DevLocal) -> bool:
    """心跳检测"""
    status, _ = general_command(local, CmdMsgID.DEVICE_TICK, b"", timeout_ms=500)
    return status == 0


def device_reboot(local: DevLocal) -> int:
    """重启设备"""
    status, _ = general_command(local, CmdMsgID.DEVICE_TICK, b"\xFF", timeout_ms=500)
    return status


# ── 报告回调注册 ────────────────────────────────────────────

def report_regist(local: DevLocal, msg_id: int,
                  cb: MsgReportCB, arg: any = None) -> int:
    """注册 Report 回调"""
    if local.hif_hdl:
        return local.hif_hdl.report_handle_regist(msg_id, cb, arg)
    return -1


# ── Internal ────────────────────────────────────────────────────

def _put_dev(local: DevLocal):
    """设备加入全局链表"""
    global _g_hdrv
    local.next_dev = _g_hdrv.pDevs
    _g_hdrv.pDevs = local


def _remove_dev(local: DevLocal):
    """从全局链表中移除设备"""
    global _g_hdrv
    prev = None
    cur = _g_hdrv.pDevs
    while cur:
        if cur is local:
            if prev:
                prev.next_dev = cur.next_dev
            else:
                _g_hdrv.pDevs = cur.next_dev
            return
        prev = cur
        cur = cur.next_dev
