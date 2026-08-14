import re
import os
import json
import calendar
from dateutil import parser
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

import requests
import numpy as np
import pandas as pd
from scipy.stats import norm
import matplotlib.pyplot as plt
from datetime import datetime, timezone
from scipy.interpolate import RBFInterpolator
from scipy.interpolate import PchipInterpolator

from api_client import TradingDeskAPI

class MarketScanner:

    def __init__(self, api, BASE_URL, USER_EMAIL, USER_PASSWORD):
        self.api = api

    def scan_market(self, markets_df, s, KEYWORDS):

        markets_df = markets_df[
            (markets_df["acceptingOrders"] == True) &
            (markets_df["enableOrderBook"] == True)
        ]
        
        markets_df = markets_df[markets_df.apply(
            lambda row: self.is_market_keyword(row, KEYWORDS), axis = 1)]
        
        CURRENCIES = {
            "bitcoin": "BTC",
            "btc": "BTC",
            "ethereum": "ETH",
            "eth": "ETH",
            "solana": "SOL",
            "sol": "SOL",
            "xrp": "XRP",
            "dogecoin": "DOGE",
            "doge": "DOGE",
        }

        markets_df["currency"] = markets_df.apply(
            lambda row: self.extract_currency(row, CURRENCIES), axis=1)

        markets_df["tokens"] = markets_df["clobTokenIds"].apply(json.loads)
        markets_df["yes_token"] = markets_df["tokens"].apply(lambda x: x[0])
        markets_df["no_token"] = markets_df["tokens"].apply(lambda x: x[1])

        book_cols = [
            "yes_book",
            "no_book",
            "yes_bid",
            "yes_ask",
            "no_bid",
            "no_ask",
        ]

        with ThreadPoolExecutor(max_workers=30) as executor:
            markets_df[book_cols] = list(executor.map(
                self.get_books, (row for _, row in markets_df.iterrows())))

        markets_df["strike"] = markets_df.question.apply(self.extract_strike)
        markets_df["expiry"] = markets_df.apply(
            lambda row: self.extract_expiry(row["question"], row["slug"]), axis=1)

        markets_df[["direction", "event_type"]] = markets_df["question"].apply(
            lambda x: pd.Series(self.parse_crypto_market_type(x))
        )

        markets_df["expiry_dt"] = pd.to_datetime(markets_df["expiry"], format="%Y%m%d", utc=True)
        markets_df["T"] = (markets_df["expiry_dt"] - datetime.now(
            timezone.utc)).dt.total_seconds() / (365.25 * 24 * 3600)
        markets_df = markets_df[markets_df["T"] > 0]

        markets_df[["iv", "model_prob", "buy_yes_fee", "sell_yes_fee", "buy_no_fee", "sell_no_fee",
            "buy_yes_ev", "sell_yes_ev", "buy_no_ev", "sell_no_ev",
            "buy_yes_kelly", "sell_yes_kelly", "buy_no_kelly", "sell_no_kelly"]] = markets_df.apply(
            lambda row: self.calculate_market_ev(row, s), axis=1)

        markets_df = markets_df.dropna(subset=["buy_yes_ev"])

        ev_cols = [
            "buy_yes_ev",
            "sell_yes_ev",
            "buy_no_ev",
            "sell_no_ev"
        ]

        markets_df["best_ev"] = markets_df[ev_cols].max(axis=1)
        markets_df["best_action"] = markets_df[ev_cols].idxmax(axis=1)

        opportunities_df = markets_df[markets_df["best_ev"] > 0].sort_values("best_ev", ascending=False)

        self.markets_df = markets_df
        self.opportunities_df = opportunities_df

        return self.markets_df, self.opportunities_df

    def is_market_keyword(self, row, KEYWORDS):
        text = (str(row["question"]) + " " + str(row.get("description", ""))).lower()

        return any(k in text for k in KEYWORDS)

    def extract_currency(self, row, CURRENCIES):
        text = f'{row.get("question", "")} {row.get("description", "")}'.lower()

        for keyword, currency in CURRENCIES.items():
            if re.search(rf'\b{re.escape(keyword)}\b', text):
                return currency

        return None

    def get_books(self, row):

        # yes_book = self.api.get_orderbook(row.yes_token)
        # no_book = self.api.get_orderbook(row.no_token)

        yes_book = self.api.orderbook(row["yes_token"])
        no_book = self.api.orderbook(row["no_token"])
        
        return pd.Series({
            "yes_book": yes_book,
            "no_book": no_book,

            "yes_bid": float(yes_book["bids"][-1]["price"]) if yes_book["bids"] else None,
            "yes_ask": float(yes_book["asks"][-1]["price"]) if yes_book["asks"] else None,

            "no_bid": float(no_book["bids"][-1]["price"]) if no_book["bids"] else None,
            "no_ask": float(no_book["asks"][-1]["price"]) if no_book["asks"] else None,
        })

    def extract_strike(self, question):

        match = re.search(r'\$([\d,]+)', question)

        if match:
            return float(match.group(1).replace(",", ""))

        return None

    def extract_expiry(self, question, slug=None):
        # Try full date in question
        # m = match
        m = re.search(
            r'(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}',
            question
        )
        if m:
            return parser.parse(m.group()).strftime("%Y%m%d")

        if slug:
            slug = slug.lower()

            # Full date in slug: december-31-2026
            m = re.search(
                r'(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})-(\d{4})',
                slug
            )
            if m:
                month, day, year = m.groups()
                return parser.parse(f"{day} {month} {year}").strftime("%Y%m%d")

            # Month only: august-2026
            m = re.search(
                r'(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{4})',
                slug
            )
            if m:
                month_name, year = m.groups()
                month = parser.parse(month_name).month
                year = int(year)

                last_day = calendar.monthrange(year, month)[1]
                return f"{year}{month:02d}{last_day:02d}"

        return None

    def parse_crypto_market_type(self, question):

        q = question.lower()

        # ----------------------
        # Direction
        # ----------------------
        if any(word in q for word in [
            "dip",
            "fall",
            "drop",
            "below",
            "under",
            "crash"
        ]):
            direction = "down"

        elif any(word in q for word in [
            "reach",
            "hit",
            "touch",
            "above",
            "over",
            "exceed"
        ]):
            direction = "up"

        else:
            direction = None

        # ----------------------
        # Event type
        # ----------------------
        if any(word in q for word in [
            "reach",
            "hit",
            "touch",
            "dip to",
            "fall to",
            "drop to",
            "crash to"
        ]):
            event_type = "touch"

        elif any(word in q for word in [
            "be above",
            "above on",
            "close above",
            "be below",
            "below on",
            "close below",
            "finish above",
            "finish below"
        ]):
            event_type = "expiry"

        else:
            event_type = None

        return {
            "direction": direction,
            "event_type": event_type
        }

    def calculate_ev(
            self,
            model_prob,
            best_ask_yes,
            best_bid_yes,
            best_ask_no,
            best_bid_no,
            fee_rate=0.07,
            kelly_fraction = 0.5
        ):
        
        """
        Expected PnL per contract for Polymarket.

        model_prob : P(YES)
        fee_rate   : taker fee rate (e.g. 0.07 for crypto markets)

        Assumes:
        - you are a taker
        - fee = fee_rate * price * (1 - price)
        - settlement has no fee
        """

        def is_valid(price):
            return pd.notna(price)

        def fee(price):
            #fee = C × feeRate × p × (1 - p)
            #Where C = number of shares traded and p = price of the shares.
            return fee_rate * price * (1 - price)

        p_yes = model_prob
        p_no = 1 - p_yes

        # -------------------------
        # BUY YES
        # -------------------------
        if is_valid(best_ask_yes):
            buy_yes_cost = best_ask_yes + fee(best_ask_yes)

            profit_if_yes = 1 - buy_yes_cost
            cost_if_no = buy_yes_cost

            # EV: profit if yes - cost if no
            buy_yes_ev = p_yes * profit_if_yes - p_no * cost_if_no

            """
            p            : probability of winning
            win_profit   : net profit if the bet wins
            loss_amount  : net loss if the bet loses

            Returns the optimal Kelly fraction.
            """

            buy_yes_kelly = kelly_fraction * (buy_yes_ev / profit_if_yes) # fee adjusted kelly

            # -------------------------
            # SELL YES (short YES)
            # -------------------------
            sell_yes_credit = best_bid_yes - fee(best_bid_yes)

            profit_if_no = sell_yes_credit
            cost_if_yes = 1 - sell_yes_credit # need to pay the remaining of the $1 out of the credit you got, if yes happened

            # EV: profit if yes - cost if no
            sell_yes_ev = p_no * profit_if_no - p_yes * cost_if_yes

            sell_yes_kelly = kelly_fraction * (sell_yes_ev / profit_if_no)

        else:
            buy_yes_ev = np.nan
            sell_yes_ev = np.nan
            buy_yes_kelly = np.nan
            sell_yes_kelly = np.nan
            
        # -------------------------
        # BUY NO
        # -------------------------
        if is_valid(best_ask_no):
            buy_no_cost = best_ask_no + fee(best_ask_no)

            profit_if_no = 1 - buy_no_cost
            cost_if_yes = buy_no_cost

            buy_no_ev = p_no * profit_if_no - p_yes * cost_if_yes

            buy_no_kelly = kelly_fraction * (buy_no_ev / profit_if_no)

            # -------------------------
            # SELL NO (short NO)
            # -------------------------
            sell_no_credit = best_bid_no - fee(best_bid_no)

            profit_if_yes = sell_no_credit
            cost_if_no = 1 - sell_no_credit

            sell_no_ev = p_yes * profit_if_yes - p_no * cost_if_no

            sell_no_kelly = kelly_fraction * (sell_no_ev / profit_if_yes)

        else:
            buy_no_ev = np.nan
            sell_no_ev = np.nan
            buy_no_kelly = np.nan
            sell_no_kelly = np.nan
        
        print("buy_yes_ev:", buy_yes_ev)
        print("sell_yes_ev:", sell_yes_ev)
        print("buy_no_ev:", buy_no_ev)
        print("sell_no_ev:", sell_no_ev)
        print("buy_yes_kelly:", buy_yes_kelly)
        print("sell_yes_kelly:", sell_yes_kelly)
        print("buy_no_kelly:", buy_no_kelly)
        print("sell_no_kelly:", sell_no_kelly)

        return {
            "buy_yes_fee": fee(best_ask_yes),
            "sell_yes_fee": fee(best_bid_yes),
            "buy_no_fee": fee(best_ask_no),
            "sell_no_fee": fee(best_bid_no),
            "buy_yes_ev": buy_yes_ev,
            "sell_yes_ev": sell_yes_ev,
            "buy_no_ev": buy_no_ev,
            "sell_no_ev": sell_no_ev,
            "buy_yes_kelly": buy_yes_kelly,
            "sell_yes_kelly": sell_yes_kelly,
            "buy_no_kelly": buy_no_kelly,
            "sell_no_kelly": sell_no_kelly,
        }

    def calculate_market_ev(self, row, s, confidence=0.7):

        currency = row["currency"]
        required_strike = row["strike"]

        T = row["T"]
        fee_rate = row["feeSchedule"]["rate"]

        iv = s.get_iv_from_surface(exchange=s, currency=currency, required_strike=required_strike, T=T)
        print('iv', iv)

        if iv is None:
            return None

        if row["event_type"] == "touch":
            p_touch_above, p_touch_below = s.prob_touch(
                        spot=s.data[currency]["weighted_spot"],
                        required_strike=required_strike,
                        iv=iv,
                        T=T,
                        r=0)
            
            if row["direction"] == "up":
                model_prob = p_touch_above

            elif row["direction"] == "down":
                model_prob = p_touch_below

        if row["event_type"] == "expiry":
            p_finish_above, p_finish_below = s.prob_finish(
                        spot=s.data[currency]["weighted_spot"], 
                        required_strike=required_strike, 
                        iv=iv,
                        T=T,
                        r=0)

            if row["direction"] == "up":
                model_prob = p_finish_above

            elif row["direction"] == "down":
                model_prob = p_finish_below

        market_prob = (row["yes_bid"] + row["yes_ask"]) / 2
        model_prob_adjusted = (confidence * model_prob + (1-confidence) * market_prob)

        print("")
        print(currency)

        ev = self.calculate_ev(
            model_prob=model_prob,
            best_ask_yes=row["yes_ask"],
            best_bid_yes=row["yes_bid"],
            best_ask_no=row["no_ask"],
            best_bid_no=row["no_bid"],
            fee_rate=fee_rate
        )

        return pd.Series({
            "iv": iv,
            "model_prob": model_prob,
            **ev
        })