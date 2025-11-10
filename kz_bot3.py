# kz_final_bot.py
#!/usr/bin/env python3
"""
冠军级动量再平衡 bot
策略：涨幅 × $10,000 = 目标仓位
自动卖弱买强 + 银行级风控 + 真实 Horus 数据
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

import time
import argparse
from datetime import datetime
from typing import Dict, List
import pandas as pd
import numpy as np
from loguru import logger
from roostoo_client import RoostooClient
from horus_client import HorusClient

# ==================== 配置 ====================
INITIAL_CASH = 1_000_000

DRY_RUN = False
SYMBOLS = [
    "BTC/USD", "ETH/USD", "XRP/USD", "BNB/USD", "SOL/USD", "DOGE/USD",
    "TRX/USD", "ADA/USD", "XLM/USD", "WBTC/USD", "SUI/USD", "HBAR/USD",
    "LINK/USD", "BCH/USD", "WBETH/USD", "UNI/USD", "AVAX/USD", "SHIB/USD",
    "TON/USD", "LTC/USD", "DOT/USD", "PEPE/USD", "AAVE/USD", "ONDO/USD",
    "TAO/USD", "WLD/USD", "APT/USD", "NEAR/USD", "ARB/USD", "ICP/USD",
    "ETC/USD", "FIL/USD", "TRUMP/USD", "OP/USD", "ALGO/USD", "POL/USD",
    "BONK/USD", "ENA/USD", "ENS/USD", "VET/USD", "SEI/USD", "RENDER/USD",
    "FET/USD", "ATOM/USD", "VIRTUAL/USD", "SKY/USD", "BNSOL/USD", "RAY/USD",
    "TIA/USD", "JTO/USD", "JUP/USD", "QNT/USD", "FORM/USD", "INJ/USD",
    "STX/USD"
]
BASE_PER_PERCENT = 10_000  # 每涨 1% 分配 $10,000
INTERVAL = 900  # 15 分钟调仓一次

logger.add("champion_bot.log", rotation="10 MB", level="INFO", enqueue=True)

# ==================== 风控铁律 ====================
class RiskManager:
    def __init__(self):
        self.max_drawdown = 0.10
        self.max_per_asset = 0.35
        self.daily_loss_limit = 0.04
        self.peak = INITIAL_CASH
        self.today_pnl = 0.0

    def check(self, total_value: float, positions: Dict) -> bool:


        if total_value <= 0 or self.peak <= 0:
            logger.warning("资产或峰值为 0，跳过风控计算")
            return False



        # 1. 最大回撤
        self.peak = max(self.peak, total_value)
        if (self.peak - total_value) / self.peak > self.max_drawdown:
            logger.warning("风控触发：最大回撤超10%")
            return False

        # 2. 单资产暴露
        for value in positions.values():
            if value / total_value > self.max_per_asset:
                logger.warning("风控触发：单币暴露超35%")
                return False

        # 3. 每日亏损熔断
        if self.today_pnl < -self.daily_loss_limit * INITIAL_CASH:
            logger.warning("风控触发：当日亏损超4%")
            return False

        return True

# ==================== 客户端 ====================
# ==================== 客户端 ====================
class ExchangeClient:
    def __init__(self):
        # 检查环境变量
        self.api_key = os.getenv("ROOSTOO_API_KEY")
        self.secret_key = os.getenv("ROOSTOO_API_SECRET")
        global DRY_RUN
        DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

        if not self.api_key or not self.secret_key:
            logger.warning("未读取到 Roostoo API Key 或 Secret，启用 DRY_RUN")
            DRY_RUN = True

        # 使用正式 API URL
        self.roostoo = RoostooClient()
        self.horus = HorusClient()
        logger.info(f"[{self.ts()}] 客户端就绪 | DRY_RUN={DRY_RUN}")

    def ts(self):
        return datetime.utcnow().strftime("%m-%d %H:%M:%S")

    def fetch_price(self, symbol: str) -> float:
        """获取最新价格（优先 Horus，其次模拟价）"""
        try:
            asset = symbol.split("/")[0]
            price = self.horus.get_latest_price(asset)
            logger.info(f"{asset}/USD 最新价: {price}")
            return price
        except Exception as e:
            logger.warning(f"{symbol} Horus 获取失败: {e}，使用模拟价")
            return self.horus._mock_price(asset)

    def get_balance(self):
        res = self.roostoo._sign_and_request("GET", "/v3/balance")
        logger.info(f"[Roostoo] get_balance raw response: {res}")
        if not res.get("Success"):
            logger.warning(f"Roostoo get_balance failed: {res.get('ErrMsg')}")
            return {}
        wallet = res.get("SpotWallet", {})
        # 展平成 { "USD": 50000, ... }
        flat = {asset: info["Free"] for asset, info in wallet.items()}
        return flat

    def place_order(self, symbol: str, side: str, amount: float):
        if amount == 0:
            return
        if DRY_RUN:
            logger.info(f"[DRY] 模拟 {side} {abs(amount):.6f} {symbol}")
            return {"status": "filled"}
        try:
            return self.roostoo.place_order(symbol, side, abs(amount))
        except Exception as e:
            logger.error(f"下单失败 {symbol}: {e}")

# ==================== 核心策略 ====================
class DynamicMomentumBot:
    def __init__(self, client):
        self.client = client
        self.risk = RiskManager()

    def step(self):
        try:
            # 1. 获取最新价格
            prices = {sym: self.client.fetch_price(sym) for sym in SYMBOLS}
            logger.info(f"价格: { {s: f'${p:,.0f}' for s,p in prices.items()} }")

            # 2. 获取余额 + 计算当前价值
            balance = self.client.get_balance()
            usd = balance.get("USD", 0)
            positions = {}
            for sym in SYMBOLS:
                asset = sym.split("/")[0]
                amount = balance.get(asset, 0)
                positions[sym] = amount * prices[sym]

            total_value = usd + sum(positions.values())
            logger.info(f"总资产: ${total_value:,.0f} | 现金: ${usd:,.0f}")

            if self.risk.peak == INITIAL_CASH and total_value < INITIAL_CASH:
                logger.info(f"初始化峰值校准为 ${total_value:,.0f}")
                self.risk.peak = total_value

            # 3. 风控检查
            if not self.risk.check(total_value, positions):
                logger.info("风控暂停交易，观望中...")
                return

            # 4. 计算动量得分（过去15分钟涨幅）
            momentum_targets = {}  # {sym: target_usd}
            for sym in SYMBOLS:
                try:
                    data = self.client.horus.get_market_price(
                        pair=sym.replace("/", ""), limit=2
                    )
                    ret = (data[0]["close"] / data[1]["close"]) - 1
                    target_usd = ret * BASE_PER_PERCENT * 100  # 百分比 → 美元
                    momentum_targets[sym] = max(target_usd, -usd * 0.5)  # 防卖空
                except:
                    momentum_targets[sym] = 0

            logger.info(f"动量目标: { {s: f'${v:,.0f}' for s,v in momentum_targets.items() } }")

            # 5. 再平衡：卖弱买强
            for sym, target_usd in momentum_targets.items():
                current_usd = positions[sym]
                diff_usd = target_usd - current_usd

                # 限制单资产暴露
                if current_usd + diff_usd > total_value * 0.35:
                    diff_usd = total_value * 0.35 - current_usd

                if abs(diff_usd) > 500:  # 最小交易额
                    amount = diff_usd / prices[sym]
                    side = "buy" if amount > 0 else "sell"
                    self.client.place_order(sym, side, amount)
                    logger.info(f"→ {side.upper()} {abs(amount):.6f} {sym} (${abs(diff_usd):,.0f})")
                 

        except Exception as e:
            logger.error(f"step 错误: {e}", exc_info=True)

    def run(self):
        while True:
            self.step()
            time.sleep(INTERVAL)

# ==================== 主程序 ====================
if __name__ == "__main__":
    client = ExchangeClient()
    bot = DynamicMomentumBot(client)
    bot.run()