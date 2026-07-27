"""
HAL 日志抽象层 (对应 hal_log.h + port_log.h)
"""

import logging
import sys

_logger = logging.getLogger("hif")

# 日志级别映射
_levels = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def set_level(level: str):
    _logger.setLevel(_levels.get(level, logging.INFO))


def print_log(fmt: str, *args):
    _logger.info(fmt, *args)


def debug(fmt: str, *args):
    _logger.debug(fmt, *args)


def info(fmt: str, *args):
    _logger.info(fmt, *args)


def warning(fmt: str, *args):
    _logger.warning(fmt, *args)


def error(fmt: str, *args):
    _logger.error(fmt, *args)
