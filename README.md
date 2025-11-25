<h1 style="font-size: 28px; text-align: center;">
Trading Bot for Hackathon
</h1>
<h2 style="font-size: 22px; text-align: center;">
Group 48 Chenyu ZHANG
</h2>

* An automated cryptocurrency trading bot designed for a hackathon competition
* Interacts with the Horus API to perform real-time trading operations, manage balances, and execute a parameter-driven trading strategy

<br>
<br>

## Strategy Overview

**Core Algorithm**: `Price Change % × $10,000 = Target Position`

* Buy strong performers and sell weak performers
* Dynamic position sizing based on base value and market signals
* Set minimum order requirements to manage fees and reduce "noise" trades
* Rebalances every hour based on real-time market data
* Enforces strict risk controls to protect capital

<br>
<br>

## Dynamic Momentum Rebalancing Explained

### STEP 1: Measure Momentum
Every hour, for each of the 56 cryptocurrencies, calculate its recent performance.
```python
ret = (recent_data[1]["price"] / recent_data[0]["price"]) 1
```


### STEP 2: Size Positions
Translates all momentum signals into a concrete trading decision using a formula:
```python
BASE_PER_PERCENT = 2000
target_usd = ret * BASE_PER_PERCENT
```

Calculate the difference between the target and the current
```python
current_usd = positions[sym] # get data from API
diff_usd = target_usd current_usd
```

<br>
<br>

## Minimum Order Rules & Trade Management

### 1.Amount Filter Layer Minimum Trade Value
Ensure each trade meaningfully impacts the portfolio, avoiding "noise trading."
```python
abs(diff_usd) > 50
```


### 2.Precision Layer Step Size and Precision Adjustment
Ensure order quantities meet exchange precision and step size requirements, avoiding format rejection.
```python
rule = TRADE_RULES[sym]
step = rule["step_size"]

amount = math.floor(amount / step) * step # Round down to step multiple
amount = round(amount, rule["qty_precision"]) # Round to required precision
```

<br>
<br>

## Risk Management
Whenever trigger risk control, stop trade


### 1.Momentum Signal Validation
Ensure only reliable, calculable momentum signals drive trading decisions.
```python
if sym not in TRADE_RULES:
    logger.error(f"❌ symbol {sym} not in TRADE_RULES，Skipping")
    continue
```
```python
if len(data) >= 2:
    recent_data = data[-2:]
    ret = (recent_data[1]["price"] / recent_data[0]["price"]) 1
    target_usd = ret * BASE_PER_PERCENT
    momentum_targets[sym] = max(target_usd, -usd * 0.5)
    logger.info(f"{asset} Return: {ret:.4%}, Target position: ${target_usd:,.0f}")
        else:
            logger.warning(f"{asset} Insufficient data, only {len(data)} points")
            momentum_targets[sym] = 0     
```
```python
except Exception as e:
    logger.error(f"{sym} Momentum calculation error: {e}")
    momentum_targets[sym] = 0
```


### 2.Sell-Side Protection: No Over-Selling
Prevent attempting to sell more than current holdings, avoiding short positions and exchange errors.
```python
if diff_usd < 0:
    current_amount = 0.0
    asset = sym.split("/")[0]
    current_amount = float(balance.get(asset, 0) or 0)
    amount = diff_usd / prices[sym]
    if abs(amount) > current_amount:
        amount = -current_amount
else:
    amount = diff_usd / prices[sym]
```


### 3.Buy-Side Protection: Cash Buffer Protection: 0.5%
Maintain liquidity and prevent over-leverage by preserving cash reserves.
```python
if diff_usd > 0:
    max_buyable = usd * 0.995
    if diff_usd > max_buyable:
        diff_usd = max_buyable
```


### 4.Maximum Drawdown Protection: 10%
Triggers complete trading suspension if 10% drawdown from peak is breached
```python
self.max_drawdown = 0.10
self.peak = max(self.peak, total_value)
if (self.peak total_value) / self.peak > self.max_drawdown:
    logger.warning("Risk control triggered: Maximum drawdown exceeds 10%")
    return False
```


### 5.Single Asset Exposure Limit: 35%
Prevent over-concentration in any single cryptocurrency, ensuring proper diversification.
```python
self.max_per_asset = 0.35
for value in positions.values():
    if value / total_value > self.max_per_asset:
        logger.warning("Risk control triggered: Single asset exposure exceeds 35%")
            return False
```


### 6.Daily Loss Circuit Breaker: 4%
Automatic shutdown if daily losses exceed 4% of starting capital.
```python
self.daily_loss_limit = 0.04
if self.today_pnl < -self.daily_loss_limit * self.initial_cash:
    logger.warning("Risk control triggered: Daily loss exceeds 4%")
    return False
```