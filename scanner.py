import re
import json
import calendar
import numpy as np
import pandas as pd
from dateutil import parser
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

class MarketScanner1:

    def __init__(self, api):
        self.api = api


    def scan_market(self, markets_df):

        markets_df = markets_df[
            (markets_df["acceptingOrders"] == True) &
            (markets_df["enableOrderBook"] == True)
        ]

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

        return markets_df
    
class MarketScanner:

    def __init__(self, api):
        self.api = api


    def scan_market(self, markets_df, s, crypto_tag_ids):

        markets_df = markets_df[
            (markets_df["acceptingOrders"] == True) &
            (markets_df["enableOrderBook"] == True)
        ]

        markets_df = markets_df[markets_df["tags"].apply(lambda tags: any(
            str(tag["id"]) in crypto_tag_ids for tag in tags))].copy()
        
        # CURRENCIES = {
        #     "bitcoin": "BTC",
        #     "btc": "BTC",
        #     "ethereum": "ETH",
        #     "eth": "ETH",
        #     "solana": "SOL",
        #     "sol": "SOL",
        #     "xrp": "XRP",
        #     "dogecoin": "DOGE",
        #     "doge": "DOGE",
        #     "hyperliquid": "HYPE",
        #     "zcash": "ZEC"
        # }

        CURRENCIES = {
            "bitcoin": "BTC",
            "btc": "BTC",
            "ethereum": "ETH",
            "eth": "ETH",
        }

        markets_df["currency"] = markets_df.apply(
            lambda row: self.extract_currency(row, CURRENCIES), axis=1)

        markets_df["strike"] = markets_df["question"].apply(self.extract_strike)
        markets_df["expiry"] = markets_df.apply(
            lambda row: self.extract_expiry(row["question"], row["slug"]), axis=1)

        markets_df[["direction", "event_type"]] = markets_df["question"].apply(
            lambda x: pd.Series(self.parse_crypto_market_type(x))
        )

        markets_df["expiry_dt"] = pd.to_datetime(markets_df["expiry"], format="%Y%m%d", utc=True)
        markets_df["T"] = (markets_df["expiry_dt"] - datetime.now(
            timezone.utc)).dt.total_seconds() / (365.25 * 24 * 3600)

        markets_df = markets_df[markets_df["T"] > 0]
        markets_df = markets_df.dropna(subset=["currency", "strike", "expiry", "direction", "event_type"])

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

        # new sorting order
        cols = ["question", "endDate", "yes_ask", "yes_bid", "no_ask", "no_bid", "model_prob"]

        markets_df = markets_df[cols + [c for c in markets_df.columns if c not in cols]]
        opportunities_df = opportunities_df[cols + [c for c in opportunities_df.columns if c not in cols]]
        arb_candidates_df = opportunities_df.copy()
        arb_candidates_df = arb_candidates_df.sort_values(by=["currency", "event_type", "direction", "strike"])

        self.markets_df = markets_df
        self.opportunities_df = opportunities_df
        self.arb_candidates_df = arb_candidates_df

        return self.markets_df, self.opportunities_df, self.arb_candidates_df


    def extract_currency(self, row, CURRENCIES):

        text = f'{row.get("question", "")} {row.get("description", "")}'.lower()

        for keyword, currency in CURRENCIES.items():
            if re.search(rf'\b{re.escape(keyword)}\b', text):
                return currency

        return None


    def extract_strike(self, question):
    
        match = re.search(
            r'\$([\d,]+(?:\.\d+)?)([kmb])?\b',
            question,
            re.IGNORECASE
        )

        if not match:
            return None

        value = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()

        multiplier = {
            "k": 1e3,
            "m": 1e6,
            "b": 1e9,
        }

        return value * multiplier.get(suffix, 1)


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
    

    def get_books(self, row):

        yes_book = self.api.orderbook(row["yes_token"])
        no_book = self.api.orderbook(row["no_token"])

        yes_bids = yes_book.get("bids", [])
        yes_asks = yes_book.get("asks", [])

        no_bids = no_book.get("bids", [])
        no_asks = no_book.get("asks", [])

        return pd.Series({
            "yes_book": yes_book,
            "no_book": no_book,

            # Highest price someone is bidding
            "yes_bid": (max(float(x["price"]) for x in yes_bids) if yes_bids else None),

            # Lowest price someone is asking
            "yes_ask": (min(float(x["price"]) for x in yes_asks) if yes_asks else None),

            # Highest price someone is bidding
            "no_bid": (max(float(x["price"]) for x in no_bids) if no_bids else None),

            # Lowest price someone is asking
            "no_ask": (min(float(x["price"]) for x in no_asks) if no_asks else None)
        })


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

        def fee(price, fee_rate):
            #fee = C × feeRate × p × (1 - p)
            #Where C = number of shares traded and p = price of the shares.
            return fee_rate * price * (1 - price)

        p_yes = model_prob
        p_no = 1 - p_yes

        # -------------------------
        # BUY YES
        # -------------------------
        if is_valid(best_ask_yes):
            buy_yes_cost = best_ask_yes + fee(best_ask_yes, fee_rate)

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
            sell_yes_credit = best_bid_yes - fee(best_bid_yes, fee_rate)

            profit_if_no = sell_yes_credit
            cost_if_yes = 1 - sell_yes_credit # need to pay the remaining of the $1 out of the credit you got, if yes happened

            # EV: profit if yes - cost if no
            sell_yes_ev = p_no * profit_if_no - p_yes * cost_if_yes

            sell_yes_kelly = kelly_fraction * (sell_yes_ev / profit_if_no)

        else:
            print("price not valid")
            buy_yes_ev = np.nan
            sell_yes_ev = np.nan
            buy_yes_kelly = np.nan
            sell_yes_kelly = np.nan
            
        # -------------------------
        # BUY NO
        # -------------------------
        if is_valid(best_ask_no):
            buy_no_cost = best_ask_no + fee(best_ask_no, fee_rate)

            profit_if_no = 1 - buy_no_cost
            cost_if_yes = buy_no_cost

            buy_no_ev = p_no * profit_if_no - p_yes * cost_if_yes

            buy_no_kelly = kelly_fraction * (buy_no_ev / profit_if_no)

            # -------------------------
            # SELL NO (short NO)
            # -------------------------
            sell_no_credit = best_bid_no - fee(best_bid_no, fee_rate)

            profit_if_yes = sell_no_credit
            cost_if_no = 1 - sell_no_credit

            sell_no_ev = p_yes * profit_if_yes - p_no * cost_if_no

            sell_no_kelly = kelly_fraction * (sell_no_ev / profit_if_yes)

        else:
            print("price not valid")
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
        print("")

        return {
            "buy_yes_fee": fee(best_ask_yes, fee_rate),
            "sell_yes_fee": fee(best_bid_yes, fee_rate),
            "buy_no_fee": fee(best_ask_no, fee_rate),
            "sell_no_fee": fee(best_bid_no, fee_rate),
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

        print("question:", row["question"])
        print("event type:", row["event_type"])
        print("direction:", row["direction"])
        print("currency:", currency)
        print("required strike:", required_strike)
        print('iv:', iv)

        if iv is None:
            return None

        if row["event_type"] == "touch":   
            
            if row["direction"] == "up":
                model_prob = s.prob_touch_above(spot=s.data[currency]["weighted_spot"],
                    required_strike=required_strike, iv=iv, T=T, r=0, q=0)

            elif row["direction"] == "down":
                model_prob = s.prob_touch_below(spot=s.data[currency]["weighted_spot"],
                    required_strike=required_strike, iv=iv, T=T, r=0, q=0)

        if row["event_type"] == "expiry":
            p_finish_above, p_finish_below = s.prob_finish(spot=s.data[currency]["weighted_spot"], 
                    required_strike=required_strike, iv=iv, T=T, r=0)

            if row["direction"] == "up":
                model_prob = p_finish_above

            elif row["direction"] == "down":
                model_prob = p_finish_below

        market_prob = (row["yes_bid"] + row["yes_ask"]) / 2
        model_prob_adjusted = (confidence * model_prob + (1-confidence) * market_prob)

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


    def scan_arbitrage(self, arb_candidates_df):

        def fee(price, fee_rate):
            #fee = C × feeRate × p × (1 - p)
            #Where C = number of shares traded and p = price of the shares.
            return fee_rate * price * (1 - price)
        
        cross_market_arbs = []
        cross_market_arb_columns = [
            "currency",
            "event_type",
            "direction",
            "A_strike",
            "A_question",
            "A_id",
            "A_side",
            "A_price",
            "A_cost",
            "B_strike",
            "B_question",
            "B_id",
            "B_side",
            "B_price",
            "B_cost",
            "total_cost",
            "guaranteed_profit",
        ]

        for (currency, event_type, direction, strike), group_df in arb_candidates_df.groupby(["currency", "event_type", "direction", "strike"]):
            n = len(group_df)
            if n == 2:
                print(group_df)

                A = group_df.iloc[0]
                B = group_df.iloc[1]

                yes_A = A["yes_ask"] + fee(A["yes_ask"], A["feeSchedule"]["rate"])
                no_B = B["no_ask"] + fee(B["no_ask"], B["feeSchedule"]["rate"])

                yes_B = B["yes_ask"] + fee(B["yes_ask"], B["feeSchedule"]["rate"])
                no_A = A["no_ask"] + fee(A["no_ask"], A["feeSchedule"]["rate"])
                
                arb_1 = yes_A + no_B
                arb_2 = yes_B + no_A

                guaranteed_profit_1 = 1 - arb_1
                guaranteed_profit_2 = 1 - arb_2

                print("arb_1:", arb_1)
                print("arb_2:", arb_2)
                print("guaranteed_profit_1:", guaranteed_profit_1)
                print("guaranteed_profit_2:", guaranteed_profit_2)
                print("")

                if guaranteed_profit_1 > 0:
                    cross_market_arbs.append({
                    "currency": currency,
                    "event_type": event_type,
                    "direction": direction,

                    # Lower leg
                    "A_strike": A["strike"],
                    "A_question": A["question"],
                    "A_id": A["id"],
                    "A_side": "Yes",
                    "A_price": A["yes_ask"],
                    "A_cost": yes_A,

                    # Higher leg
                    "B_strike": B["strike"],
                    "B_question": B["question"],
                    "B_id": B["id"],
                    "B_side": "No",
                    "B_price": B["no_ask"],
                    "B_cost": no_B,

                    # Arb
                    "total_cost": arb_1,
                    "guaranteed_profit": guaranteed_profit_1,
                })

                if guaranteed_profit_2 > 0:
                    cross_market_arbs.append({
                    "currency": currency,
                    "event_type": event_type,
                    "direction": direction,

                    # Lower leg
                    "A_strike": A["strike"],
                    "A_question": A["question"],
                    "A_id": A["id"],
                    "A_side": "No",
                    "A_price": A["no_ask"],
                    "A_cost": no_A,

                    # Higher leg
                    "B_strike": B["strike"],
                    "B_question": B["question"],
                    "B_id": B["id"],
                    "B_side": "Yes",
                    "B_price": B["yes_ask"],
                    "B_cost": yes_B,

                    # Arb
                    "total_cost": arb_2,
                    "guaranteed_profit": guaranteed_profit_2,
                })

        cross_market_arb_df = pd.DataFrame(cross_market_arbs, columns=cross_market_arb_columns)


        vertical_arbs = []
        vertical_arb_columns = [
            "currency",
            "event_type",
            "direction",
            "lower_strike",
            "lower_question",
            "lower_id",
            "lower_side",
            "lower_price",
            "lower_cost",
            "higher_strike",
            "higher_question",
            "higher_id",
            "higher_side",
            "higher_price",
            "higher_cost",
            "total_cost",
            "guaranteed_profit",
        ]

        for (currency, event_type, direction), group_df in arb_candidates_df.groupby(["currency", "event_type", "direction"]):
            group_df = group_df.sort_values("strike").reset_index(drop=True)
            n = len(group_df)
            
            if direction == "up":
                print("up\n")

                for i in range(1, n):
                    lower = group_df.iloc[i - 1]
                    higher = group_df.iloc[i]

                    lower_cost = lower["yes_ask"] + fee(lower["yes_ask"], lower["feeSchedule"]["rate"])

                    higher_cost = higher["no_ask"] + fee(higher["no_ask"], higher["feeSchedule"]["rate"])

                    cost = lower_cost + higher_cost
                    guaranteed_profit = 1 - cost

                    print("lower_cost:", lower_cost)
                    print("higher_cost:", higher_cost)
                    print("cost:", cost)
                    print("guaranteed_profit:", guaranteed_profit)
                    print("")

                    if guaranteed_profit > 0:
                        vertical_arbs.append({
                        "currency": currency,
                        "event_type": event_type,
                        "direction": direction,

                        # Lower leg
                        "lower_strike": lower["strike"],
                        "lower_question": lower["question"],
                        "lower_id": lower["id"],
                        "lower_side": "Yes",
                        "lower_price": lower["yes_ask"],
                        "lower_cost": lower_cost,

                        # Higher leg
                        "higher_strike": higher["strike"],
                        "higher_question": higher["question"],
                        "higher_id": higher["id"],
                        "higher_side": "No",
                        "higher_price": higher["no_ask"],
                        "higher_cost": higher_cost,

                        # Arb
                        "total_cost": cost,
                        "guaranteed_profit": guaranteed_profit,
                    })

            elif direction == "down":
                print("down\n")

                for i in range(1, n):
                    lower = group_df.iloc[i - 1]
                    higher = group_df.iloc[i]

                    lower_cost = lower["no_ask"] + fee(lower["no_ask"], lower["feeSchedule"]["rate"])

                    higher_cost = higher["yes_ask"] + fee(higher["yes_ask"], higher["feeSchedule"]["rate"])

                    cost = lower_cost + higher_cost
                    guaranteed_profit = 1 - cost

                    print("lower_cost:", lower_cost)
                    print("higher_cost:", higher_cost)
                    print("cost:", cost)
                    print("guaranteed_profit:", guaranteed_profit)
                    print("")

                    if guaranteed_profit > 0:
                        vertical_arbs.append({
                        "currency": currency,
                        "event_type": event_type,
                        "direction": direction,

                        # Lower leg
                        "lower_strike": lower["strike"],
                        "lower_question": lower["question"],
                        "lower_id": lower["id"],
                        "lower_side": "No",
                        "lower_price": lower["no_ask"],
                        "lower_cost": lower_cost,

                        # Higher leg
                        "higher_strike": higher["strike"],
                        "higher_question": higher["question"],
                        "higher_id": higher["id"],
                        "higher_side": "Yes",
                        "higher_price": higher["yes_ask"],
                        "higher_cost": higher_cost,

                        # Arb
                        "total_cost": cost,
                        "guaranteed_profit": guaranteed_profit,
                    })

        vertical_arb_df = pd.DataFrame(vertical_arbs, columns=vertical_arb_columns)

        return cross_market_arb_df, vertical_arb_df