"""性能分析装饰器"""
import time
from functools import wraps
from typing import Callable, Any


def profile_endpoint(func: Callable) -> Callable:
    """
    性能分析装饰器，用于测量 API 端点的执行时间
    
    记录：
    - SQL 执行和业务逻辑耗时
    - 总逻辑耗时
    """
    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        # 1. DB 准备耗时记录 (通过 contextvars 或手动标记)
        start_time = time.perf_counter()
        
        # 执行业务逻辑 (DB 查询)
        db_start = time.perf_counter()
        result = await func(*args, **kwargs)
        db_end = time.perf_counter()
        
        # 2. 序列化耗时记录 (FastAPI 在返回 Response 时处理序列化)
        # 注意：此处记录的是逻辑返回后的时间点
        print(f"--- Profiling: {func.__name__} ---")
        print(f"SQL Execution & Logic: {(db_end - db_start):.4f}s")
        print(f"Total Logic Duration: {(time.perf_counter() - start_time):.4f}s")
        
        return result
    return wrapper
