import requests
import os
import numpy as np
import pandas as pd
from scipy.stats import norm
import matplotlib.pyplot as plt
from datetime import datetime, timezone
from scipy.interpolate import RBFInterpolator
from scipy.interpolate import PchipInterpolator

from collections import deque

class Portfolio():

    def sync_fills(self, api, fills_df):

        def get_all_trades(api):
            all_trades = []
            next_cursor = None

            while True:
                response = api.trades(next_cursor=next_cursor)

                trades = response.get("trades", [])
                all_trades.extend(trades)

                next_cursor = response.get("next_cursor")

                # Stop if no trades
                if not trades:
                    break

                # "LTE=" is the terminal cursor returned by this API
                if not next_cursor or next_cursor == "LTE=":
                    break

        new_fills = []
        trades = get_all_trades(api)

        for trade in trades:
            print(trade)

            fill_id = trade["id"]

            if fill_id in fills_df["fill_id"].values:
                continue

            new_fills.append({
                "fill_id": fill_id,
                "order_id": trade.get("order_id"),
                "condition_id": trade["market"],
                "token_id": trade["asset_id"],
                "outcome": trade.get("outcome"),
                "side": trade["side"],
                "price": float(trade["price"]),
                "shares": float(trade["size"]),
                "fee": float(trade.get("fee", 0)),
                "timestamp": pd.to_datetime(int(trade["match_time"]), unit="s", utc=True),
            })

        # Nothing new
        if not new_fills:
            return fills_df

        # Append new rows
        fills_df = pd.concat([fills_df, pd.DataFrame(new_fills)], ignore_index=True)
        fills_df["timestamp"] = fills_df["timestamp"] = (pd.to_datetime(fills_df["timestamp"], utc=True).astype("datetime64[ms, UTC]"))

        return fills_df

    # Doesnt cancel filled orders, historical order ledger
    def sync_orders(self, api, orders_df):

        api_orders = api.list_orders()

        for order in api_orders["orders"]:

            order_id = order["id"]
            mask = orders_df["order_id"] == order_id

            if not mask.any():

                order_row = {
                    "order_id": order_id,
                    "condition_id": order.get("market"),
                    "token_id": order.get("asset_id"),
                    "outcome": None,
                    "side": order.get("side"),
                    "price": order.get("price"),
                    "requested_size": order.get("original_size"),
                    "filled_size": order.get("size_matched", 0),
                    "remaining_size": (order.get("original_size", 0) - order.get("size_matched", 0)),
                    "status": order.get("status"),
                    "created_at": order.get("created_at"),
                    "cancelled_at": None,
                }

                orders_df = pd.concat([orders_df, pd.DataFrame([order_row])], ignore_index=True)

            else:
                idx = orders_df.index[mask][0]

                orders_df.loc[idx, "status"] = order.get("status")
                orders_df.loc[idx, "filled_size"] = order.get("size_matched", 0)
                orders_df.loc[idx, "remaining_size"] = (order.get("original_size", 0) - order.get("size_matched", 0))

        return orders_df

    #FIFO matching
    def reconstruct_positions_fifo(self, fills_df):
        """
        Reconstruct current Polymarket positions using FIFO.

        Expected fills_df columns:
            trade_id
            order_id
            condition_id
            token_id
            outcome
            side
            price
            shares
            fee
            timestamp

        Returns:
            positions_df:
                Current open positions with FIFO cost basis.

            realized_df:
                Realized P&L from SELL trades.
        """

        positions = []
        realized = []

        # Process each token independently.
        for token_id, trades in fills_df.groupby("token_id"):

            # FIFO queue of open BUY lots.
            # Each lot contains:
            #   remaining shares
            #   price
            #   fee_per_share
            buy_lots = deque()

            realized_pnl = 0.0
            realized_shares = 0.0
            realized_fees = 0.0

            # Very important: process chronologically.
            trades = trades.sort_values("timestamp")

            for _, trade in trades.iterrows():

                side = trade["side"]
                shares = float(trade["shares"])
                price = float(trade["price"])
                fee = float(trade.get("fee", 0) or 0)

                if side == "BUY":

                    # Store this BUY as a FIFO lot.
                    fee_per_share = fee / shares if shares > 0 else 0

                    buy_lots.append({
                        "shares": shares,
                        "price": price,
                        "fee_per_share": fee_per_share,
                    })

                elif side == "SELL":

                    remaining_to_sell = shares
                    sell_fee_per_share = fee / shares if shares > 0 else 0

                    while remaining_to_sell > 0:

                        if not buy_lots:
                            print(f"SELL exceeds available position for token_id={token_id}")
                            raise ValueError(f"SELL exceeds available position for token_id={token_id}")

                        lot = buy_lots[0]

                        matched_shares = min(remaining_to_sell, lot["shares"])

                        # Cost of the shares being sold.
                        buy_cost = (matched_shares * lot["price"])

                        # Allocate the original BUY fee
                        # to the shares being sold.
                        buy_fee = (matched_shares * lot["fee_per_share"])

                        # Proceeds from the SELL.
                        sell_proceeds = (matched_shares * price)

                        # Allocate SELL fee to this FIFO match.
                        sell_fee = (matched_shares * sell_fee_per_share)

                        # Realized P&L.
                        pnl = (sell_proceeds - sell_fee - buy_cost - buy_fee)

                        realized_pnl += pnl
                        realized_shares += matched_shares
                        realized_fees += buy_fee + sell_fee

                        # Reduce the FIFO lot.
                        lot["shares"] -= matched_shares
                        remaining_to_sell -= matched_shares

                        # Remove empty lot.
                        if lot["shares"] <= 1e-12:
                            buy_lots.popleft()

            # --------------------------------------------------
            # Remaining BUY lots = current position
            # --------------------------------------------------
            remaining_shares = sum(lot["shares"] for lot in buy_lots)

            if remaining_shares <= 0:
                continue

            remaining_cost = sum(lot["shares"] * (lot["price"] + lot["fee_per_share"]) for lot in buy_lots)

            avg_entry_price = (remaining_cost / remaining_shares)

            # Use the first trade for metadata.
            first_trade = trades.iloc[0]

            positions.append({
                "condition_id": first_trade["condition_id"],
                "token_id": token_id,
                "outcome": first_trade.get("outcome"),
                "shares": remaining_shares,
                "cost_basis": remaining_cost,
                "avg_entry_price": avg_entry_price,
                "realized_pnl": realized_pnl,
                "realized_shares": realized_shares,
                "realized_fees": realized_fees,
            })

            realized.append({
                "condition_id": first_trade["condition_id"],
                "token_id": token_id,
                "outcome": first_trade.get("outcome"),
                "realized_shares": realized_shares,
                "realized_pnl": realized_pnl,
                "realized_fees": realized_fees,
            })

        positions_df = pd.DataFrame(positions)
        realized_df = pd.DataFrame(realized)

        return positions_df, realized_df


    def mark_positions_to_market(self, positions_df, markets_df):
        """
        Add current market prices and unrealized PnL to open positions.

        positions_df columns:
            token_id
            shares
            avg_entry_price

        markets_df columns:
            token_id
            price

        Returns:
            positions_df with:
                current_price
                market_value
                cost_basis
                unrealized_pnl
                unrealized_return
        """

        positions_df = positions_df.copy()

        if positions_df.empty:
            print("positions_df is empty, returning mtm")
            return positions_df

        # Keep only the columns we need from markets
        
        # Create YES token -> YES bid mapping
        yes_prices = markets_df[["yes_token", "yes_bid"]].rename(
            columns={
                "yes_token": "token_id",
                "yes_bid": "current_price"
            }
        )

        # Create NO token -> NO bid mapping
        no_prices = markets_df[["no_token", "no_bid"]].rename(
            columns={
                "no_token": "token_id",
                "no_bid": "current_price"
            }
        )

        # Combine YES and NO tokens
        prices = pd.concat([yes_prices, no_prices], ignore_index=True)

        # Make sure there is only one price per token
        prices = prices.drop_duplicates("token_id")

        # Match price to position
        positions_df = positions_df.merge(prices, on="token_id", how="left")

        # Current market value
        positions_df["market_value"] = (positions_df["shares"] * positions_df["current_price"])

        # Original cost
        positions_df["cost_basis"] = (positions_df["shares"] * positions_df["avg_entry_price"])

        # Unrealized PnL
        positions_df["unrealized_pnl"] = (positions_df["market_value"] - positions_df["cost_basis"])

        # Return
        positions_df["unrealized_return"] = (positions_df["unrealized_pnl"] / positions_df["cost_basis"])

        return positions_df
    
    def calculate_equity(self, api, positions_df, realized_df, equity_df):
        """
        Calculate the current account equity and P&L.

        Uses the last row of equity_df to determine
        previous_equity and previous_timestamp.
        """

        # ---------------------------------------------------------
        # 0. Get previous equity snapshot
        # ---------------------------------------------------------

        if equity_df.empty:
            previous_equity = None
        else:
            previous_row = equity_df.iloc[-1]
            previous_equity = float(previous_row["equity"])

        # ---------------------------------------------------------
        # 1. Get current cash
        # ---------------------------------------------------------
        cash = float(api.balance()["balance"])

        # ---------------------------------------------------------
        # 2. Market value of open positions
        # ---------------------------------------------------------
        if positions_df.empty:
            market_value = 0.0
            unrealized_pnl = 0.0

        else:
            if "market_value" in positions_df.columns:
                market_value = positions_df["market_value"].sum()
            else:
                market_value = (positions_df["shares"] * positions_df["current_price"]).sum()

            if "unrealized_pnl" in positions_df.columns:
                unrealized_pnl = positions_df["unrealized_pnl"].sum()

            else:
                unrealized_pnl = (positions_df["shares"] * (
                        positions_df["current_price"]
                        - positions_df["avg_entry_price"]
                    )).sum()

        # ---------------------------------------------------------
        # 3. Realized P&L
        # ---------------------------------------------------------
        if realized_df.empty:
            realized_pnl = 0.0
        else:
            realized_pnl = realized_df["realized_pnl"].sum()

        # ---------------------------------------------------------
        # 4. Total equity
        # ---------------------------------------------------------
        equity = cash + market_value

        # ---------------------------------------------------------
        # 5. Return
        # ---------------------------------------------------------
        if previous_equity is None or previous_equity == 0:
            period_return = None
        else:
            period_return = equity / previous_equity - 1

        # ---------------------------------------------------------
        # 6. Return new row
        # ---------------------------------------------------------
        equity_row = {
            "timestamp": datetime.now(timezone.utc),
            "cash": cash,
            "market_value": market_value,
            "equity": equity,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "period_return": period_return,
        }

        equity_df = pd.concat([equity_df, pd.DataFrame([equity_row])], ignore_index=True)
        equity_df["timestamp"] = pd.to_datetime(equity_df["timestamp"], utc=True)
        
        # ---------------------------------------------------------
        # 7. Calculate Sharpe / Sortino
        # ---------------------------------------------------------

        returns = equity_df["daily_return"].dropna()

        if len(returns) >= 2:

            # Sharpe
            volatility = returns.std(ddof=1)

            if volatility > 0:
                sharpe = returns.mean() / volatility
            else:
                sharpe = np.nan

            # Sortino
            downside_returns = returns[returns < 0]

            if len(downside_returns) > 0:
                downside_deviation = np.sqrt(
                    (downside_returns ** 2).mean()
                )

                sortino = returns.mean() / downside_deviation

            else:
                sortino = np.nan

        else:
            sharpe = np.nan
            sortino = np.nan

        # ---------------------------------------------------------
        # 8. Store metrics
        # ---------------------------------------------------------

        equity_df.loc[equity_df.index[-1], "sharpe"] = sharpe
        equity_df.loc[equity_df.index[-1], "sortino"] = sortino

        # ---------------------------------------------------------
        # 9. Print
        # ---------------------------------------------------------

        print(f"Equity:  {equity:,.2f}")
        print(f"Return:  {period_return:.4%}" if pd.notna(period_return) else "Return: N/A")
        print(f"Sharpe:  {sharpe:.4f}" if pd.notna(sharpe) else "Sharpe: N/A")
        print(f"Sortino: {sortino:.4f}" if pd.notna(sortino) else "Sortino: N/A")

        return equity_df

    def format_signal_exit(self, best_action, row, pos, current_bid, 
                           client_oid, hold_ev, exit_ev, positions_df, EXIT_EV_THRESHOLD):

        signal = "EXIT" if best_action == "exit" else "HOLD"

        rows = [
            ("Market", row["question"]),
            ("Position", pos["outcome"]),
            ("Shares", pos["shares"]),
            ("Current Bid", current_bid),
            ("Exit EV", exit_ev),
            ("Hold EV", hold_ev),
            ("Unrealized Pnl", positions_df["unrealized_pnl"]),
            ("Unrealized Return", positions_df["unrealized_return"]),
            ("Exit EV threshold", EXIT_EV_THRESHOLD),
        ]

        print("=" * 70)
        print(f"{signal + ' SIGNAL':^70}")
        print("=" * 70)

        for label, value in rows:
            print(f"{label:<25} : {value}")

        print("=" * 70)
        print(f"{'VARIABLES IN REQUEST':^70}")
        
        rows = [
            ("tokenId", pos["token_id"]),
            ("orderPrice", current_bid),
            ("orderSize", pos["shares"]),
            ("clientOrderId", client_oid)
        ]

        for label, value in rows:
            print(f"{label:<25} : {value}")

        print("=" * 70)

    def format_signal_entry(self, trade, outcome, best_action, normalized_size, current_ask,
                        token_id, client_oid, unrealized_pnl, cash, ENTRY_EV_THRESHOLD, MAX_POSITION):

        print("=" * 70)
        print("BUY SIGNAL")
        print("=" * 70)

        p_yes = trade["model_prob"]
        p_no = 1 - p_yes

        rows = [
            ("Market", trade["question"]),
            ("Outcome", outcome),
            ("Direction", trade["direction"]),
            ("Event Type", trade["event_type"]),
            ("Best Action", best_action),
            ("Recommended Size", normalized_size),
            ("Current Ask", current_ask),
            ("P Yes", p_yes),
            ("P No", p_no),
            ("Buy Yes EV", trade["buy_yes_ev"]),
            ("Sell No EV", trade["sell_no_ev"]),
            ("Buy No EV", trade["buy_no_ev"]),
            ("Sell Yes EV", trade["sell_yes_ev"]),
            ("Unrealized Pnl", unrealized_pnl),
            ("Cash", cash),
            ("ENTRY EV threshold", ENTRY_EV_THRESHOLD),
            ("MIN Position threshold", MAX_POSITION),
        ]

        for label, value in rows:
            print(f"{label:<25} : {value}")

        print("=" * 70)
        print(f"{'VARIABLES IN REQUEST':^70}")

        rows = [
            ("tokenId", token_id),
            ("orderPrice", current_ask),
            ("orderSize", normalized_size),
            ("clientOrderId", client_oid)
        ]

        for label, value in rows:
            print(f"{label:<25} : {value}")

        print("=" * 70)

    def save_snapshots(self, df, df_name):

        date = datetime.now().strftime("%Y%m%d")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        DATA_DIR = f"data/{date}"
        os.makedirs(DATA_DIR, exist_ok=True)
        filename = f"{DATA_DIR}/{df_name}_{timestamp}.parquet"

        df.to_parquet(filename, engine="fastparquet", index=False)

        print('save_snapshots df saved at: ', filename)

    def save(self, df, df_name):

        DATA_DIR = f"data"
        os.makedirs(DATA_DIR, exist_ok=True)
        filename = f"{DATA_DIR}/{df_name}.parquet"

        df.to_parquet(filename, engine="fastparquet", index=False)

        print('save df saved at: ', filename)
