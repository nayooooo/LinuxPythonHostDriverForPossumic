"""
HAL OS 抽象层 (Python 实现, 对应 hal_os.h/.c + port_os.c)

Python 原生多线程, 无需外部 OS 依赖:
  - 线程 = threading.Thread
  - 信号量 = threading.Semaphore / Lock
  - 临界区 = threading.Lock
  - 内存 = bytearray (Python GC 管理)
"""

import time
import threading
import logging

from .types import HostOsSem, HostOsThread, ERRCODE_SUCCESS

logger = logging.getLogger("hal.os")

# ── 临界区 ────────────────────────────────────────────────────
# 全局互斥锁, 用于关键区域保护
_critical_lock = threading.Lock()


def enter_critical() -> bool:
    """进入临界区, 返回 True"""
    _critical_lock.acquire()
    return True


def exit_critical(_flag: bool = True):
    """退出临界区"""
    _critical_lock.release()


# ── 时间戳 ────────────────────────────────────────────────────
def timestamp_us() -> int:
    """获取微秒级时间戳"""
    return int(time.monotonic() * 1_000_000)


def delay_ms(ms: int):
    """毫秒延时"""
    time.sleep(ms / 1000.0)


def delay_us(us: int):
    """微秒延时"""
    time.sleep(us / 1_000_000.0)


# ── 内存管理 ──────────────────────────────────────────────────
# Python 中内存由 GC 管理, 以下仅做语义包装

def malloc(size: int) -> bytearray:
    return bytearray(size)


def free(buf: bytearray):
    pass  # GC 自动处理


def mem_set(buf: bytearray, val: int, size: int):
    buf[:size] = bytes([val]) * size


def mem_copy(dst: bytearray, src: bytes, size: int):
    dst[:size] = src[:size]


def mem_cmp(a: bytes, b: bytes, size: int) -> int:
    if a[:size] == b[:size]:
        return 0
    return -1


# ── 线程管理 ──────────────────────────────────────────────────
_thread_registry: dict[int, threading.Thread] = {}
_next_thread_id = 1


def thread_create(name: str, entry, arg=None,
                  priority: int = 0, stack_size: int = 8192) -> HostOsThread:
    """创建后台线程, 返回线程句柄"""
    global _next_thread_id
    tid = _next_thread_id
    _next_thread_id += 1

    t = threading.Thread(target=entry, args=(arg,) if arg else (),
                         name=name, daemon=True)
    _thread_registry[tid] = t
    t.start()

    # 清理已退出的死线程引用, 防止注册表无限增长
    _reap_dead_threads()

    logger.debug(f"Thread '{name}' created (id={tid}, prio={priority})")
    return tid


def _reap_dead_threads():
    """清理已终止的线程引用"""
    dead = [tid for tid, t in _thread_registry.items() if not t.is_alive()]
    for tid in dead:
        del _thread_registry[tid]


def thread_delete(tid: HostOsThread):
    """删除线程 (Python daemon 线程自动回收, 仅移除注册)"""
    t = _thread_registry.pop(tid, None)
    if t and t.is_alive():
        t.join(timeout=2.0)


def thread_is_valid(tid: HostOsThread) -> bool:
    return tid in _thread_registry


# ── 信号量 ────────────────────────────────────────────────────
_sem_registry: dict[int, threading.Semaphore] = {}
_next_sem_id = 1


def sem_create(initial_count: int = 0, max_count: int = 1) -> HostOsSem:
    """创建信号量"""
    global _next_sem_id
    sid = _next_sem_id
    _next_sem_id += 1
    _sem_registry[sid] = threading.Semaphore(initial_count)
    logger.debug(f"Semaphore created (id={sid}, init={initial_count}, max={max_count})")
    return sid


def sem_delete(sid: HostOsSem):
    _sem_registry.pop(sid, None)  # 安全删除, 不存在也不抛异常


def sem_is_valid(sid: HostOsSem) -> bool:
    return sid in _sem_registry


def sem_take(sid: HostOsSem, timeout_ms: int = 0) -> int:
    """获取信号量

    timeout_ms:
      0      → 非阻塞 (对应 HOST_OS_TIMEOUT_NO_WAIT)
      0xFFFFFFFF → 无限等待 (HOST_OS_TIMEOUT_FOREVER)
      其他   → 等待 timeout_ms 毫秒

    返回: ERRCODE_SUCCESS 或 错误码
    """
    sem = _sem_registry.get(sid)
    if sem is None:
        return -1

    if timeout_ms == 0:
        # 0 = NO_WAIT: 非阻塞尝试
        acquired = sem.acquire(blocking=False)
    elif timeout_ms >= 0xFFFFFFFF:
        # FOREVER: 无限阻塞
        acquired = sem.acquire(timeout=None)
    else:
        acquired = sem.acquire(timeout=timeout_ms / 1000.0)

    return ERRCODE_SUCCESS if acquired else -2  # TIMEOUT


def sem_give(sid: HostOsSem) -> int:
    """释放信号量"""
    sem = _sem_registry.get(sid)
    if sem is None:
        return -1
    sem.release()
    return ERRCODE_SUCCESS


# ── 电源管理 (stub) ───────────────────────────────────────────
def pm_lock() -> int:
    """锁电源, 防止休眠"""
    return ERRCODE_SUCCESS


def pm_unlock() -> int:
    """解锁电源"""
    return ERRCODE_SUCCESS
