"""API 响应结构验证脚本"""
import sys
from typing import Any, Dict, Optional

import httpx


class Colors:
    """终端颜色"""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"


def print_pass(message: str):
    """打印通过信息"""
    print(f"{Colors.GREEN}✅ PASS{Colors.RESET}: {message}")


def print_fail(message: str):
    """打印失败信息"""
    print(f"{Colors.RED}❌ FAIL{Colors.RESET}: {message}")


def print_info(message: str):
    """打印信息"""
    print(f"{Colors.YELLOW}ℹ️  INFO{Colors.RESET}: {message}")


def test_list_endpoint(base_url: str) -> Optional[str]:
    """
    测试 GET /card/list 端点
    
    Returns:
        返回第一个卡片的 ID（用于后续测试），如果失败返回 None
    """
    print("\n" + "=" * 60)
    print("测试 1: GET /card/list")
    print("=" * 60)

    url = f"{base_url}/card/list"
    params = {"page": 1, "pageSize": 10}

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()

            # 断言 1: 状态码为 200
            if response.status_code != 200:
                print_fail(f"状态码应为 200，实际为 {response.status_code}")
                return None
            print_pass(f"状态码: {response.status_code}")

            # 解析 JSON
            data = response.json()

            # 断言 2: 响应结构匹配
            if "code" not in data or "message" not in data or "data" not in data:
                print_fail("响应缺少必要字段: code, message, data")
                return None
            print_pass("响应包含 code, message, data 字段")

            if data["code"] != 200:
                print_fail(f"code 应为 200，实际为 {data['code']}")
                return None
            print_pass(f"code: {data['code']}")

            # 断言 3: data 结构匹配 { total, page, pageSize, list }
            data_payload = data["data"]
            required_fields = ["total", "page", "pageSize", "list"]
            missing_fields = [f for f in required_fields if f not in data_payload]

            if missing_fields:
                print_fail(f"data 缺少字段: {missing_fields}")
                return None
            print_pass(f"data 包含所有必需字段: {required_fields}")

            # 验证字段类型
            if not isinstance(data_payload["total"], int):
                print_fail(f"total 应为 int，实际为 {type(data_payload['total'])}")
                return None
            print_pass(f"total 类型正确: {type(data_payload['total']).__name__}")

            if not isinstance(data_payload["list"], list):
                print_fail(f"list 应为 list，实际为 {type(data_payload['list'])}")
                return None
            print_pass(f"list 类型正确: {type(data_payload['list']).__name__}")

            # 断言 4: 检查列表项中的字段
            if len(data_payload["list"]) == 0:
                print_info("列表为空，跳过字段检查")
                return None

            first_item = data_payload["list"][0]

            # 检查 icon 字段（重命名自 imageUrl）
            if "icon" not in first_item:
                print_fail("列表项缺少 'icon' 字段")
                return None
            print_pass("列表项包含 'icon' 字段")

            # 检查 markets 字段
            if "markets" not in first_item:
                print_fail("列表项缺少 'markets' 字段")
                return None
            if not isinstance(first_item["markets"], list):
                print_fail(f"markets 应为 list，实际为 {type(first_item['markets'])}")
                return None
            print_pass("列表项包含 'markets' 字段（类型为 list）")

            # 检查 markets 中的 probability 字段
            if len(first_item["markets"]) > 0:
                first_market = first_item["markets"][0]
                if "probability" not in first_market:
                    print_fail("market 项缺少 'probability' 字段")
                    return None
                if not isinstance(first_market["probability"], (int, float)):
                    print_fail(
                        f"probability 应为数字，实际为 {type(first_market['probability'])}"
                    )
                    return None
                print_pass(
                    f"market 项包含 'probability' 字段: {first_market['probability']}"
                )

            # 获取第一个卡片的 ID 用于后续测试
            card_id = first_item.get("id")
            if not card_id:
                print_fail("列表项缺少 'id' 字段")
                return None
            print_pass(f"获取到第一个卡片 ID: {card_id}")

            print_info(f"列表总数: {data_payload['total']}")
            print_info(f"当前页: {data_payload['page']}")
            print_info(f"每页数量: {data_payload['pageSize']}")
            print_info(f"当前页项目数: {len(data_payload['list'])}")

            return card_id

    except httpx.HTTPStatusError as e:
        print_fail(f"HTTP 错误: {e.response.status_code} - {e.response.text}")
        return None
    except httpx.RequestError as e:
        print_fail(f"请求错误: {str(e)}")
        return None
    except Exception as e:
        print_fail(f"未预期的错误: {str(e)}")
        return None


def test_details_endpoint(base_url: str, card_id: str):
    """
    测试 GET /card/details 端点
    
    Args:
        base_url: API 基础 URL
        card_id: 卡片 ID
    """
    print("\n" + "=" * 60)
    print("测试 2: GET /card/details")
    print("=" * 60)

    url = f"{base_url}/card/details"
    params = {"id": card_id}

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()

            # 断言 1: 状态码为 200
            if response.status_code != 200:
                print_fail(f"状态码应为 200，实际为 {response.status_code}")
                return
            print_pass(f"状态码: {response.status_code}")

            # 解析 JSON
            data = response.json()

            # 断言 2: 响应结构匹配
            if "code" not in data or "message" not in data or "data" not in data:
                print_fail("响应缺少必要字段: code, message, data")
                return
            print_pass("响应包含 code, message, data 字段")

            if data["code"] != 200:
                print_fail(f"code 应为 200，实际为 {data['code']}")
                return
            print_pass(f"code: {data['code']}")

            # 断言 3: data 结构匹配 { id: str, ... }
            card_data = data["data"]

            if "id" not in card_data:
                print_fail("data 缺少 'id' 字段")
                return
            if not isinstance(card_data["id"], str):
                print_fail(f"id 应为 str，实际为 {type(card_data['id'])}")
                return
            print_pass(f"data 包含 'id' 字段: {card_data['id']}")

            # 断言 4: 检查 ai_analysis 字段（可以为 None）
            if "ai_analysis" not in card_data:
                print_fail("data 缺少 'ai_analysis' 字段")
                return
            print_pass(
                f"data 包含 'ai_analysis' 字段: {card_data.get('ai_analysis', 'None')}"
            )

            # 断言 5: 检查 createdAt 字段
            if "createdAt" not in card_data:
                print_fail("data 缺少 'createdAt' 字段")
                return
            print_pass(f"data 包含 'createdAt' 字段: {card_data.get('createdAt')}")

            print_info(f"卡片标题: {card_data.get('title', 'N/A')}")
            print_info(f"卡片 slug: {card_data.get('slug', 'N/A')}")

    except httpx.HTTPStatusError as e:
        print_fail(f"HTTP 错误: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        print_fail(f"请求错误: {str(e)}")
    except Exception as e:
        print_fail(f"未预期的错误: {str(e)}")


def main():
    """主函数"""
    # 配置
    base_url = "http://127.0.0.1:8000"

    # 如果提供了命令行参数，使用它作为 base_url
    if len(sys.argv) > 1:
        base_url = sys.argv[1]

    print(f"\n🚀 开始验证 API 响应结构")
    print(f"📍 目标 URL: {base_url}")

    # 测试 1: List 端点
    card_id = test_list_endpoint(base_url)

    # 测试 2: Details 端点（如果 List 测试成功）
    if card_id:
        test_details_endpoint(base_url, card_id)
    else:
        print_fail("跳过 Details 测试（List 测试失败）")

    print("\n" + "=" * 60)
    print("✅ 验证完成")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
