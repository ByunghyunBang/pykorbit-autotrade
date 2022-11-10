import time
import pyupbit
import datetime
import os
import traceback
import lineNotify
import debug_settings
import trading_settings
import yaml

access = os.getenv('UPBIT_ACCESS')
secret = os.getenv('UPBIT_SECRET')

def get_middle(value1, value2, rate=0.5):
    return value1 + (value2 - value1) * rate

def get_target_price(ohlcv_candle2, k):
    """변동성 돌파 전략으로 매수 목표가 조회"""
    df = ohlcv_candle2
    target_price = df.iloc[0]['close'] + (df.iloc[0]['high'] - df.iloc[0]['low']) * k
    return target_price

def get_target_price2(ohlcv_candle2, k):
    """변동성 돌파 전략으로 매수 목표가 조회 (어제 종가 + 오늘 최저가 가중치 반영으로 매수 목표 설정)"""
    df = ohlcv_candle2
    base = get_middle(df.iloc[0]['close'],df.iloc[1]['low'],0.6)
    target_price = base + (df.iloc[0]['high'] - df.iloc[0]['low']) * k
    return target_price

def get_candle_open(ohlcv_candle2):
    df = ohlcv_candle2
    return df.iloc[1]['open']

def get_start_time(market):
    """시작 시간 조회"""
    df = pyupbit.get_ohlcv(market, interval=candle_interval, count=1)
    start_time = df.index[0]
    return start_time

def get_balance(ticker):
    """잔고 조회"""
    balances = upbit.get_balances()
    for b in balances:
        if b['currency'] == ticker:
            if b['balance'] is not None:
                return float(b['balance'])
            else:
                return 0
    return 0

def get_current_price(market):
    """현재가 조회"""
    return pyupbit.get_orderbook(ticker=market)["orderbook_units"][0]["ask_price"]

def log(msg):
    now = datetime.datetime.now()
    print(now, msg)

def log_and_notify(msg):
    log(msg)
    if debug_settings.trading_enabled:
        now = datetime.datetime.now().replace(microsecond=0)
        notify_msg = str(now) + "\n" + msg.replace(";","\n").replace(": ",":\n")
        lineNotify.line_notify(notify_msg)

def diff_percent(n):
    return round((n - 1) * 100, 2)

def clear_flags():
    global already_buyed, meet_expected_price, emergency_sell, is_frozen, frozen_time, is_closed
    already_buyed=False
    meet_expected_price=False
    emergency_sell=False
    is_frozen=False
    frozen_time=time.time()
    is_closed=False

def set_freeze(now):
    global is_frozen, frozen_time
    is_frozen=True
    frozen_time=now

def human_readable(num):
    return format(int(num), ',')

def start_log():
    log_str = "start: market={};k={};expected_rate_p={}%;partial_sell_rate_p={}%;emergency_sell_rate_p={}%;candle_interval={}".format(
                market, k, expected_rate_p, round(partial_sell_rate,2), emergency_sell_rate_p, candle_interval
                )

    if debug_settings.trading_enabled:
        log_and_notify(log_str)
    else:
        log(log_str)

def save_status(status):
    with open(status_file, "w") as f:
        yaml.dump(status, f)

def load_status():
    try:
        with open(status_file, "r") as f:
            status = yaml.load(f, Loader=yaml.FullLoader)
    except:
        status = { 'latest_krw': None}
    return status

status_file = "trading-status.yml"

# 각종 설정
symbol = trading_settings.symbol
k = trading_settings.k
expected_rate_p = trading_settings.expected_rate_p
partial_sell_rate = trading_settings.partial_sell_rate
emergency_sell_rate_p = trading_settings.emergency_sell_rate_p
candle_interval = trading_settings.candle_interval

if candle_interval=="minute240":
    time_delta=datetime.timedelta(minutes=240)
elif candle_interval=="minute60":
    time_delta=datetime.timedelta(minutes=60)
elif candle_interval=="minute1":
    time_delta=datetime.timedelta(minutes=1)
elif candle_interval=="day":
    time_delta=datetime.timedelta(days=1)

market="KRW-{}".format(symbol)
expected_rate=expected_rate_p / 100 # 익절 조건 : 매수시점대비 몇% 상승시 매도할 것인가 (일부 매도)
emergency_sell_rate=emergency_sell_rate_p / 100
latest_krw = None

# 로그인
upbit = pyupbit.Upbit(access, secret)

# 자동매매 시작
clear_flags()
status = load_status()
is_closed = True

def candle_begin_event():
    global current_price,target_price,expected_price,emergency_sell_price,candle_open,status
    ohlcv_candle2 = pyupbit.get_ohlcv(market, interval=candle_interval, count=2)
    candle_open = get_candle_open(ohlcv_candle2)
    target_price = get_target_price(ohlcv_candle2, k)
    expected_price = target_price * (1 + expected_rate)
    emergency_sell_price = target_price * (1 - emergency_sell_rate)

    start_log()
    log_and_notify(
        "candle begin: market={};current_price={};target_price={};expected_price={};emergency_sell_price={};candle_open={};latest_krw={}"
        .format(
            market,
            human_readable(current_price),
            human_readable(target_price),
            human_readable(expected_price),
            human_readable(emergency_sell_price),
            human_readable(candle_open),
            human_readable(status['latest_krw'])
            )
        )

while True:
    try:
        now = datetime.datetime.now()
        start_time = get_start_time(market)
        end_time = start_time + time_delta - datetime.timedelta(seconds=20)

        # 거래 가능 시간: 봉시작 ~ 봉종료 20초전
        if start_time < now < end_time:

            current_price = get_current_price(market)

            if is_closed:
                clear_flags()
                candle_begin_event()
                is_closed=False

            log(
                "(no-event) diff from current: current_price={};target_price={}({}%);expected_price={}({}%);emergency_sell_price={}({}%)"
                .format(
                    human_readable(current_price),
                    human_readable(target_price - current_price), diff_percent(target_price / current_price),
                    human_readable(expected_price - current_price), diff_percent(expected_price / current_price),
                    human_readable(emergency_sell_price - current_price), diff_percent(emergency_sell_price / current_price),
                )
            )

            # Freeze 상태이면 거래하지 않음
            if is_frozen:
                continue

            # 변동성 돌파 시점에 매수
            if (not already_buyed) and (current_price >= target_price):
                krw = get_balance("KRW")
                if krw > 5000:
                    log_and_notify(
                        "buy: current_price={};target_price={};krw={}"
                        .format(
                            human_readable(current_price),
                            human_readable(target_price),
                            human_readable(krw)
                        )
                    )
                    if debug_settings.trading_enabled:
                        upbit.buy_market_order(market, krw*0.9995)
                    already_buyed = True
                    emergency_sell_price = current_price * (1 - emergency_sell_rate)

            # 기대이익실현 시점에 일부 매도
            if (not meet_expected_price) and (current_price >= expected_price):
                partial_crypto = get_balance(symbol) * partial_sell_rate
                if partial_crypto > 0.00008:
                    log_and_notify(
                        "partial sell on expected price: current_price={};expected_price={};partial_sell_rate={},partial_crypto={}"
                        .format(
                            human_readable(current_price),
                            human_readable(expected_price),
                            partial_sell_rate,
                            partial_crypto
                        )
                    )
                    if debug_settings.trading_enabled:
                        upbit.sell_market_order(market, partial_crypto)
                    meet_expected_price=True

            # 손절 : 지정된 손절시점에서 전량매도
            if (current_price <= emergency_sell_price):
                crypto = get_balance(symbol)
                if crypto > 0.00008:
                    total_krw = upbit.get_balance_t()
                    log_and_notify("emergency sell: current_price={};crypto={};crypto_balance={};total_krw={}"
                        .format(
                            human_readable(current_price), 
                            crypto,
                            human_readable(current_price * crypto),
                            human_readable(total_krw)
                        )
                    )
                    if debug_settings.trading_enabled:
                        upbit.sell_market_order(market, crypto)
                    # set_freeze(now)

        # 종료 시점
        else:
            # 종료 시점에 전량매도
            if not is_closed:
                crypto = get_balance(symbol)
                total_krw = upbit.get_balance_t()
                if crypto > 0.00008:
                    log_and_notify(
                        "closing sell: current_price={};crypto={};crypto_balance={};total_krw={}"
                        .format(
                            human_readable(current_price),
                            crypto,
                            human_readable(current_price*crypto),
                            human_readable(total_krw)
                        )
                    )
                    if debug_settings.trading_enabled:
                        upbit.sell_market_order(market, crypto)
                        time.sleep(5) # Waiting order completed

                # 현재 잔액 로그
                total_krw = upbit.get_balance_t()
                latest_krw = status['latest_krw']
                if (latest_krw is None):
                    latest_krw = total_krw
                total_krw_diff = total_krw - latest_krw
                log_and_notify(
                    "end: balance={};earned={}({}%)"
                    .format(
                        human_readable(total_krw),
                        human_readable(total_krw_diff),
                        round(total_krw_diff/latest_krw*100,2)
                    )
                )
                latest_krw = total_krw
                is_closed= True
                status['latest_krw']=latest_krw
                save_status(status)

        time.sleep(10)
    except Exception as e:
        log(e)
        traceback.print_exc()
        time.sleep(10)
