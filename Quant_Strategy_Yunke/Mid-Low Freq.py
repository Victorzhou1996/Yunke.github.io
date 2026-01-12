import os
import requests
import pandas as pd
import datetime as dt
import warnings
import yfinance as yf
import backtrader as bt
import matplotlib.pyplot as plt

# ==========================================
# 【1. 全局配置开关】
# ==========================================
DATA_SOURCE = 'YAHOO'
STRATEGY_CHOICE = 'MA'

SHOW_TRADE_LOG = False
SHOW_FINAL_REPORT = True

START_CASH = 100000.0
COMMISSION = 0.001
POSITION_PERCENT = 50

# --- 时间与标的 ---
START_DATE = '2022-06-01'
END_DATE = '2025-12-31'

if DATA_SOURCE == 'BINANCE':
    TARGET_SYMBOL = 'BTCUSDT'
    MARKET_SYMBOL = 'ETHUSDT'
else:
    TARGET_SYMBOL = 'TSLA'
    MARKET_SYMBOL = 'SPY'

warnings.filterwarnings("ignore")


# ==========================================
# 【2. 数据引擎】
# ==========================================

def clean_dataframe(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(col).lower() for col in df.columns]
    if 'adj close' in df.columns: df['close'] = df['adj close']
    keep_cols = ['open', 'high', 'low', 'close', 'volume']
    df = df[keep_cols]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None: df.index = df.index.tz_localize(None)
    return df.astype(float)


def fetch_binance_data(symbol, start_str, end_str):
    url = 'https://api.binance.com/api/v3/klines'
    start_dt = dt.datetime.strptime(start_str, '%Y-%m-%d')
    end_dt = dt.datetime.strptime(end_str, '%Y-%m-%d')
    all_dfs = []
    curr_start = start_dt
    while curr_start < end_dt:
        start_ms = int(curr_start.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        params = {'symbol': symbol, 'interval': '1d', 'startTime': start_ms, 'endTime': end_ms, 'limit': 1000}
        try:
            res = requests.get(url, params=params)
            data = res.json()
            if not data or 'code' in data: break
            tmp_df = pd.DataFrame(data).iloc[:, 0:6]
            tmp_df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            tmp_df.index = pd.to_datetime(tmp_df['timestamp'], unit='ms')
            all_dfs.append(tmp_df)
            curr_start = tmp_df.index[-1] + dt.timedelta(minutes=1)
            if len(tmp_df) < 1000: break
        except:
            break
    return pd.concat(all_dfs) if all_dfs else None


def load_data_unified(symbol):
    if not symbol: return None
    cache_file = f"{DATA_SOURCE}_{symbol}_{START_DATE}_{END_DATE}.csv"
    if os.path.exists(cache_file):
        return pd.read_csv(cache_file, index_col=0, parse_dates=True)
    df = None
    if DATA_SOURCE == 'BINANCE':
        df = fetch_binance_data(symbol, START_DATE, END_DATE)
    else:
        df = yf.download(symbol, start=START_DATE, end=END_DATE, auto_adjust=True)
    if df is not None and not df.empty:
        df = clean_dataframe(df)
        df.to_csv(cache_file)
        return df
    return None


# ==========================================
# 【3. 策略定义】
# ==========================================

class BaseStrategy(bt.Strategy):
    def log(self, txt, dname=None):
        if SHOW_TRADE_LOG:
            dt_val = self.datas[0].datetime.date(0)
            print(f"{dt_val.isoformat()} [{dname or ''}] {txt}")


class MaCrossStrategy(BaseStrategy):
    params = (('fast', 5), ('slow', 30))

    def __init__(self):
        self.inds = {d: bt.ind.CrossOver(bt.ind.SMA(d, period=self.params.fast),
                                         bt.ind.SMA(d, period=self.params.slow)) for d in self.datas}

    def next(self):
        for d in self.datas:
            pos = self.getposition(d).size
            if not pos and self.inds[d] > 0:
                self.buy(data=d)
            elif pos and self.inds[d] < 0:
                self.close(data=d)


class UltimateStrategy(BaseStrategy):
    params = (('p1', 7), ('p2', 14), ('p3', 28), ('low', 30), ('high', 70))

    def __init__(self):
        self.ult = {d: bt.ind.UltimateOscillator(d, p1=self.params.p1, p2=self.params.p2, p3=self.params.p3) for d in
                    self.datas}

    def next(self):
        for d in self.datas:
            pos = self.getposition(d).size
            if not pos and self.ult[d] < self.params.low:
                self.buy(data=d)
            elif pos and self.ult[d] > self.params.high:
                self.close(data=d)


class MomentumStrategy(BaseStrategy):
    params = (('low', 30), ('high', 70))

    def __init__(self):
        self.ultosc = bt.ind.UltimateOscillator(self.datas[0])
        self.market_close = self.datas[1].close

    def next(self):
        if not self.position:
            if self.ultosc < self.params.low and self.market_close[0] > self.market_close[-1]:
                self.buy(data=self.datas[0])
        elif self.ultosc > self.params.high:
            self.close(data=self.datas[0])


# ==========================================
# 【4. 主运行程序】
# ==========================================
if __name__ == '__main__':
    target_data = load_data_unified(TARGET_SYMBOL)
    market_data = load_data_unified(MARKET_SYMBOL) if STRATEGY_CHOICE == 'MOM' else None

    if target_data is not None:
        cerebro = bt.Cerebro()
        cerebro.adddata(bt.feeds.PandasData(dataname=target_data), name=TARGET_SYMBOL)
        if STRATEGY_CHOICE == 'MOM' and market_data is not None:
            cerebro.adddata(bt.feeds.PandasData(dataname=market_data), name=MARKET_SYMBOL)

        cerebro.addstrategy(
            {'MA': MaCrossStrategy, 'ULTOSC': UltimateStrategy, 'MOM': MomentumStrategy}[STRATEGY_CHOICE])
        cerebro.broker.setcash(START_CASH)
        cerebro.broker.setcommission(commission=COMMISSION)
        cerebro.addsizer(bt.sizers.PercentSizer, percents=POSITION_PERCENT)

        # 分析器
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', riskfreerate=0.02, annualize=True,
                            timeframe=bt.TimeFrame.Days)

        print(f"--- 启动回测 | 策略: {STRATEGY_CHOICE} | 目标: {TARGET_SYMBOL} ---")
        results = cerebro.run()
        strat = results[0]

        # --- 总结计算 ---
        if SHOW_FINAL_REPORT:
            final_v = cerebro.broker.getvalue()
            total_ret = (final_v - START_CASH) / START_CASH

            # 1. 计算回测跨越的年数
            start_dt = dt.datetime.strptime(START_DATE, '%Y-%m-%d')
            end_dt = dt.datetime.strptime(END_DATE, '%Y-%m-%d')
            duration_years = (end_dt - start_dt).days / 365.25

            # 2. 计算年化收益率 (CAGR)
            # 公式: (最终价值/初始价值)^(1/年数) - 1
            if duration_years > 0:
                annual_ret = (pow(final_v / START_CASH, 1 / duration_years) - 1) * 100
            else:
                annual_ret = 0.0

            dd_stats = strat.analyzers.dd.get_analysis()
            sharpe_stats = strat.analyzers.sharpe.get_analysis()
            sharpe_ratio = sharpe_stats.get('sharperatio', 0)

            print('\n' + '█' * 50)
            print(f'   【 {STRATEGY_CHOICE} 策略回测总结报告 】')
            print('█' * 50)
            print(f' • 回测周期     :  {START_DATE} 至 {END_DATE} ({duration_years:.2f} 年)')
            print(f' • 交易标的     :  {TARGET_SYMBOL}')
            print(f' • 初始资产     :  {START_CASH:,.2f}')
            print(f' • 最终资产     :  {final_v:,.2f}')
            print(f' • 累计收益率   :  {total_ret * 100:.2f}%')
            print(f' • 年化收益率   :  {annual_ret:.2f}% (CAGR)')
            print(f' • 最大资金回撤 :  {dd_stats.max.drawdown:.2f}%')
            print(f' • 夏普比率     :  {sharpe_ratio:.2f}')
            print('█' * 50 + '\n')

        try:
            cerebro.plot(style='candle', iplot=False, barup='red', bardown='green')
        except:
            pass
    else:
        print("错误：数据加载失败。")