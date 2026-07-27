"""
HAL 存储抽象层 (对应 hal_store.h + port_store.c)

用于固件镜像文件的读写操作
"""

import logging

logger = logging.getLogger("hal.store")


def store_open(path: str) -> tuple[int, int] | None:
    """打开存储文件, 返回 (handle, file_len)"""
    try:
        f = open(path, "rb+")
        data = f.read()
        sz = len(data)
        # 返回文件句柄 (用 id 表示)
        return (id(f), sz)
    except OSError as e:
        logger.error(f"store_open failed: {e}")
        return None


def store_close(handle: int):
    pass  # Python GC


def store_read(handle: int, offset: int, size: int) -> bytes:
    """从存储读取指定偏移和大小"""
    # 简化实现: 每次重新打开文件
    import gc
    for obj in gc.get_objects():
        if hasattr(obj, 'read') and id(obj) == handle:
            obj.seek(offset)
            return obj.read(size)
    return b""
