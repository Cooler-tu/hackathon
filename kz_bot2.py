#!/usr/bin/env python3
"""
冠军级动量再平衡 bot（含模拟价格系统）
策略：涨幅 × $10,000 = 目标仓位
自动卖弱买强 + 银行级风控 + 模拟行情支持
"""

import os
import time
import random
from datetime import datetime
from typing import Dict
from loguru import logger
from roostoo_client import RoostooClient
from horus_client import HorusClient

# ==================== 配置 ====================
INITIAL_CASH = 1_000_000
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
FORCE_HORUS_422 = os.getenv("FORCE_HORUS_422", "false").lower() == "true"

SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]
BASE_PER_PERCENT = 10_000  # 每涨 1% 分配 $10,000
INTERVAL = 10  # 每10秒调仓一次（测试用）

logger.add("champion_bot.log", rotation="10 MB", level="INFO", enqueue=True)

# ==================== 风控 ====================
class RiskManager:
    def __init__(self):
        self.max_drawdown = 0.10
        self.max_per_asset = 0.35
        self.daily_loss_limit = 0.04
        self.peak = INITIAL_CASH
        self.today_pnl = 0.0

    def check(self, total_value: float, positions: Dict) -> bool:
        self.peak = max(self.peak, total_value)
        if (self.peak - total_value) / self.peak > self.max_drawdown:
            logger.warning("风控触发：最大回撤超10%")
            return False

        for value in positions.values():
            if value / total_value > self.max_per_asset:
                logger.warning("风控触发：单币暴露超35%")
                return False

        if self.today_pnl < -self.daily_loss_limit * INITIAL_CASH:
            logger.warning("风控触发：当日亏损超4%")
            return False

        return True


# ==================== 模拟行情模块 ====================
class MockMarket:
    """模拟行情波动系统"""
    def __init__(self):
        self.prices = {"BTC/USD": 68000, "ETH/USD": 3500, "SOL/USD": 180}
        self.prev_prices = self.prices.copy()

    def update(self):
        """随机±1%波动"""
        self.prev_prices = self.prices.copy()
        for sym in self.prices:
            change = random.uniform(-0.01, 0.01)
            self.prices[sym] *= (1 + change)
        return self.prices

    def get_price(self, sym: str) -> float:
        return self.prices[sym]

    def get_return(self, sym: str) -> float:
        """计算上一轮到这一轮的涨幅"""
        return (self.prices[sym] / self.prev_prices[sym]) - 1


# ==================== 客户端 ====================
class ExchangeClient:
    def __init__(self):
        self.roostoo = RoostooClient()
        self.horus = HorusClient()
        self.mock = MockMarket()
        logger.info(f"[{self.ts()}] 客户端就绪 | DRY_RUN={DRY_RUN}, FORCE_HORUS_422={FORCE_HORUS_422}")

    def ts(self):
        return datetime.utcnow().strftime("%m-%d %H:%M:%S")

    def fetch_price(self, symbol: str) -> float:
        """获取价格：优先真实 Horus，否则使用模拟行情"""
        if FORCE_HORUS_422:
            self.mock.update()
            return self.mock.get_price(symbol)

        try:
            asset = symbol.split("/")[0]
            return self.horus.get_latest_price(asset)
        except Exception as e:
            logger.warning(f"{symbol} Horus 获取失败: {e}，使用模拟行情")
            self.mock.update()
            return self.mock.get_price(symbol)

    def get_balance(self) -> Dict[str, float]:
        if DRY_RUN:
            return {"USD": 500_000, "BTC": 2.0, "ETH": 15.0, "SOL": 200.0}
        return self.roostoo.get_balance()

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


# ==================== 策略核心 ====================
class DynamicMomentumBot:
    def __init__(self, client):
        self.client = client
        self.risk = RiskManager()

    def step(self):
        try:
            # 1. 更新并获取最新价格
            prices = {sym: self.client.fetch_price(sym) for sym in SYMBOLS}
            logger.info(f"价格: { {s: f'${p:,.2f}' for s,p in prices.items()} }")

            # 2. 获取余额与仓位
            balance = self.client.get_balance()
            usd = balance.get("USD", 0)
            positions = {}
            for sym in SYMBOLS:
                asset = sym.split("/")[0]
                positions[sym] = balance.get(asset, 0) * prices[sym]

            total_value = usd + sum(positions.values())
            logger.info(f"总资产: ${total_value:,.0f} | 现金: ${usd:,.0f}")

            # 3. 风控
            if not self.risk.check(total_value, positions):
                logger.info("风控暂停交易，观望中...")
                return

            # 4. 计算动量得分
            momentum_targets = {}
            for sym in SYMBOLS:
                ret = 0.0
                if FORCE_HORUS_422:
                    ret = self.client.mock.get_return(sym)
                else:
                    try:
                        data = self.client.horus.get_market_price(pair=sym.replace("/", ""), limit=2)
                        ret = (data[0]["close"] / data[1]["close"]) - 1
                    except Exception:
                        ret = 0

                target_usd = ret * BASE_PER_PERCENT * 100
                momentum_targets[sym] = positions[sym] + target_usd
            logger.info(f"动量涨幅: { {s: f'{self.client.mock.get_return(s)*100:.2f}%' for s in SYMBOLS} }")
            logger.info(f"动量目标: { {s: f'${v:,.0f}' for s,v in momentum_targets.items()} }")

            # 5. 再平衡
            for sym, target_usd in momentum_targets.items():
                current_usd = positions[sym]
                diff_usd = target_usd - current_usd

                if current_usd + diff_usd > total_value * 0.35:
                    diff_usd = total_value * 0.35 - current_usd

                if abs(diff_usd) > 500:
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
