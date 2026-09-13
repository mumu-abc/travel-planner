"""Pytest 全局配置"""
import pytest


def pytest_configure(config):
    """注册自定义 marker"""
    config.addinivalue_line("markers", "slow: 需要 LLM API 调用的慢速测试")
