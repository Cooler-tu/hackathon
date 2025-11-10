# horus_client.py
import requests
import os
from dotenv import load_dotenv
from loguru import logger

load_dotenv()
HORUS_API_KEY = os.getenv("HORUS_API_KEY", "")
HORUS_BASE_URL = "https://api-horus.com"

class HorusClient:
    def __init__(self):
        if not HORUS_API_KEY:
            raise ValueError("HORUS_API_KEY not set in .env")
        self.session = requests.Session()
        # ✅ 改为正确 Header 名
        self.session.headers.update({
            "X-API-Key": HORUS_API_KEY,
            "Content-Type": "application/json"
        })

    def _request(self, endpoint, params=None):

        if os.getenv("FORCE_HORUS_422", "").lower() in ("1", "true", "yes"):
            logger.warning("FORCE_HORUS_422 enabled — forcing a 422 error for testing")
            # 人为抛出一个 HTTPError，和你日志里看到的格式一致
            raise requests.HTTPError("422 Client Error: Unprocessable Entity for url")
        """统一请求入口"""
        url = HORUS_BASE_URL + endpoint
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            # 新 API 返回 array，而不是 { "data": ... }
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return data
        except Exception as e:
            logger.error(f"Horus API error: {e}")
            raise

    def get_market_price(self, asset="BTC", interval="1d", start=None, end=None, fmt="json"):
        """
        获取历史价格
        文档参数：
            asset (str): "BTC", "ETH", ...
            interval (str): "1d" | "1h" | "15m"
            start (int): 起始时间戳（秒）
            end (int): 结束时间戳（秒）
            fmt (str): "json" | "csv"
        """
        params = {
            "asset": asset,
            "interval": interval,
            "format": fmt
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        return self._request("/market/price", params)

    def get_latest_price(self, asset="BTC"):
        """
        获取当前最新价格（取返回数组最后一条数据）
        """
        try:
            data = self.get_market_price(asset=asset, interval="1d")
            if isinstance(data, list) and len(data) > 0:
                return data[-1]["price"]
            else:
                logger.warning(f"{asset} Horus 返回空数据，用模拟价")
                return self._mock_price(asset)
        except Exception:
            logger.warning(f"{asset} Horus 失败，用模拟价")
            return self._mock_price(asset)

    def _mock_price(self, asset):
        """失败时生成模拟价"""
        mock_prices = {"BTC": 68000, "ETH": 3500, "SOL": 180}
        return mock_prices.get(asset, 100)

    # 下面两个方法保留原有逻辑（未改动）
    def get_defi_tvl(self, chain=None, protocol=None, limit=100):
        """获取 TVL 数据"""
        params = {"limit": limit}
        if chain:
            params["chain"] = chain
        if protocol:
            params["protocol"] = protocol
        return self._request("/defi/tvl", params)

    def get_transaction_count(self, chain=None, limit=100):
        """获取交易数量"""
        params = {"limit": limit}
        if chain:
            params["chain"] = chain
        return self._request("/transaction/count", params)
