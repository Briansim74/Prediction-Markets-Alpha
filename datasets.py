import requests
import numpy as np
import pandas as pd
from scipy.stats import norm
import matplotlib.pyplot as plt
from datetime import datetime, timezone
from scipy.interpolate import RBFInterpolator
from scipy.interpolate import PchipInterpolator

class OptionSurface:

    def initialize(self, currencies, exchanges):
        self.data = {}

        for currency in currencies:
            weighted_spot = self.get_weighted_spot(currency, exchanges)
            curve_df = self.get_curve_df(currency, exchanges)
            surface_df = self.get_surface(currency, exchanges)
            f_variance, params = self.build_variance_surface(weighted_spot, surface_df)

            self.data[currency] = {
                "weighted_spot": weighted_spot,
                "curve_df": curve_df,
                "surface_df": surface_df,
                "f_variance": f_variance,
                "params": params
            }

    def get_weighted_spot(self, currency, exchanges):

        spots = [ex.data[currency]["spot"] for ex in exchanges if currency in ex.data]
        weights = [ex.data[currency]["volume"] for ex in exchanges if currency in ex.data]

        weighted_spot = np.average(spots, weights=weights)

        return weighted_spot

    def get_curve_df(self, currency, exchanges):
            
        dfs = [ex.data[currency]["curve_df"] for ex in exchanges if currency in ex.data]

        curve_df = pd.concat(dfs, ignore_index=True)

        curve_df = (curve_df.groupby("strike").apply(
            lambda x: np.average(x.mark_iv, weights=x.open_interest_notional)).reset_index(name="mark_iv")
        )

        return curve_df

    def get_surface(self, currency, exchanges):
    
        dfs = [ex.data[currency]["df"] for ex in exchanges if currency in ex.data]

        surface_df = pd.concat(dfs, ignore_index=True)

        surface_df = surface_df.groupby(["strike", "T"]).apply(
            lambda x: np.average(x.total_variance, weights=x.open_interest_notional)).reset_index(name="total_variance")
        
        return surface_df

    def build_variance_surface(self, weighted_spot, surface_df):

        K = surface_df["strike"].to_numpy()
        T = surface_df["T"].to_numpy()

        # Log-moneyness
        k = np.log(K / weighted_spot)

        # Scale coordinates
        k_mean = k.mean()
        k_std = k.std()
        T_mean = T.mean()
        T_std = T.std()

        k_scaled = (k - k_mean) / k_std
        T_scaled = (T - T_mean) / T_std

        query_points = np.column_stack([k_scaled, T_scaled])

        # Interpolate log(total variance) so the reconstructed
        # total variance is always positive.
        log_variance = np.log(surface_df["total_variance"].to_numpy())

        f_variance = RBFInterpolator(
            query_points,
            log_variance,
            kernel="thin_plate_spline",
            smoothing=0.001
        )

        params = {
            "k_mean": k_mean,
            "k_std": k_std,
            "T_mean": T_mean,
            "T_std": T_std
        }

        return f_variance, params

    def get_iv_from_curve(self, exchange, currency, required_strike):

        curve_df = exchange.data[currency]["curve_df"]

        # Question: Will BTC hit 100000 by 31DEC26?

        # It prevents wild overshoots and spurious oscillations found in standard cubic splines, 
        # making it ideal for monotonic data, bounded physical measurements, and financial curves
        f = PchipInterpolator(curve_df.strike, curve_df.mark_iv)

        iv = float(f(required_strike))

        print(f"{currency} {required_strike} strike iv: {iv}")

        return iv

    def get_iv_from_surface(self, exchange, currency, required_strike, T):

        weighted_spot = exchange.data[currency]["weighted_spot"]
        f_variance = exchange.data[currency]["f_variance"]
        params = exchange.data[currency]["params"]

        k_mean = params["k_mean"]
        k_std = params["k_std"]
        T_mean = params["T_mean"]
        T_std = params["T_std"]

        k = np.log(required_strike / weighted_spot)

        k_scaled = (k - k_mean) / k_std
        T_scaled = (T - T_mean) / T_std

        query_points = [[k_scaled, T_scaled]]

        log_variance = f_variance(query_points)[0]
        total_variance = np.exp(log_variance)

        iv = np.sqrt(total_variance / T)

        return float(iv)

    def plot_curve(self, exchange, currency, exchange_name, target_expiry_str):
    
        curve_df = exchange.data[currency]["curve_df"]
    
        plt.plot(curve_df.strike,
                curve_df.mark_iv * 100,   # convert decimal IV to %
                marker="o"
            )
        
        plt.xlabel("Strike")
        plt.ylabel("Implied Volatility (%)")
        plt.title(f"{exchange_name} {currency} {target_expiry_str} IV Smile")
        plt.grid(True)

        plt.show()

    def plot_surface(self, exchange, currency):

        weighted_spot = exchange.data[currency]["weighted_spot"]
        surface_df = exchange.data[currency]["surface_df"]
        f_variance = exchange.data[currency]["f_variance"]
        params = exchange.data[currency]["params"]
        
        k_mean = params["k_mean"]
        k_std = params["k_std"]
        T_mean = params["T_mean"]
        T_std = params["T_std"]

        fig = plt.figure(figsize=(10, 7))
        
        ax = fig.add_subplot(111, projection="3d")

        # Create grid
        strike_grid = np.linspace(surface_df["strike"].min(), surface_df["strike"].max(), 50)
        T_grid = np.linspace(surface_df["T"].min(), surface_df["T"].max(), 50)

        X, Y = np.meshgrid(strike_grid, T_grid)

        K = X.ravel()
        T = Y.ravel()
        
        # --------------------------------
        # Apply SAME scaling as training
        # --------------------------------
        k = np.log(K / weighted_spot)

        k_scaled = (k - k_mean) / k_std
        T_scaled = (T - T_mean) / T_std

        query_points = np.column_stack([k_scaled, T_scaled])

        # Query interpolator
        log_variance_grid = f_variance(query_points).reshape(X.shape)
        variance_grid = np.exp(log_variance_grid)

        print("variance min:", np.nanmin(variance_grid))
        print("variance max:", np.nanmax(variance_grid))

        print("T min:", np.nanmin(Y))
        print("T max:", np.nanmax(Y))

        print("negative variance count:", np.sum(variance_grid < 0))
        print("zero T count:", np.sum(Y == 0))
        print("nan variance count:", np.sum(np.isnan(variance_grid)))

        # Convert variance back to IV
        IV_grid = np.sqrt(variance_grid / Y)

        # Plot surface
        ax.plot_surface(X, Y, IV_grid, cmap="viridis", alpha=0.8)

        mark_iv = np.sqrt(surface_df["total_variance"] / surface_df["T"])

        # Plot original points
        ax.scatter(
            surface_df["strike"],
            surface_df["T"],
            mark_iv,
            color="red",
            s=20
        )

        ax.set_xlabel("Strike")
        ax.set_ylabel("Time to Expiry (years)")
        ax.set_zlabel("Implied Volatility")

        plt.show()

    def prob_finish(self, spot, required_strike, iv, T, r=0):

        d2 = (np.log(spot / required_strike) + (r - 0.5 * iv ** 2) * T) / (iv * np.sqrt(T))

        p_finish_above = norm.cdf(d2)
        p_finish_below = 1 - p_finish_above

        print("p_finish_above:", p_finish_above, "p_finish_below:", p_finish_below)

        return p_finish_above, p_finish_below

    def prob_touch(self, spot, required_strike, iv, T, r=0, paths=100000, time_steps=365):

        # dS = rSdt + σSdW
        dt = T / time_steps
        Z = np.random.normal(0, 1, size=(paths, time_steps)) # vectorization

        daily_return = ((r - 0.5 * iv ** 2) * dt + iv * np.sqrt(dt) * Z)

        prices = spot * np.exp(np.cumsum(daily_return, axis=1))

        hit_above = prices.max(axis=1) >= required_strike
        hit_below = prices.min(axis=1) <= required_strike

        p_touch_above = hit_above.mean()
        p_touch_below = hit_below.mean()

        print("p_touch_above:", p_touch_above, "p_touch_below:", p_touch_below)

        return p_touch_above, p_touch_below

class Deribit:
    def __init__(self, currencies, target_expiry):
         self.data = {}

         for currency in currencies:
            spot, volume = self.get_spot(currency)
            df = self.get_df(currency)
            curve_df = self.get_curve_df(df, target_expiry)

            self.data[currency] = {
                "spot": spot,
                "volume": volume,
                "df": df,
                "curve_df": curve_df
            }

    def get_spot(self, currency):
        url = "https://www.deribit.com/api/v2/public/ticker"
        params = {"instrument_name": f"{currency}_USDT"}

        data = requests.get(url, params=params).json()["result"]
        
        spot = float(data["last_price"])
        volume = float(data["stats"]["volume"])

        print("spot:", spot, "volume24h:", volume)

        return spot, volume

    def get_df(self, currency):
        
        url = "https://www.deribit.com/api/v2/public/get_instruments"
        params = {
            "currency": f"{currency}",
            "kind": "option"
        }

        instruments = requests.get(url=url, params=params).json()["result"]
        instruments_df = pd.DataFrame(instruments)

        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
        params = {
            "currency": f"{currency}",
            "kind": "option"
        }

        quotes = requests.get(url=url, params=params).json()["result"]
        quotes_df = pd.DataFrame(quotes)

        df = instruments_df.merge(quotes_df, on="instrument_name", how="left")
        df["open_interest_notional"] = df["open_interest"] * df["contract_size"]
        df["mark_iv"] = df["mark_iv"].astype(float) / 100

        parts = df["instrument_name"].str.split("-")

        df["expiry"] = pd.to_datetime(parts.str[1], format="%d%b%y").dt.strftime("%Y%m%d")
        df["expiry_dt"] = pd.to_datetime(parts.str[1], format="%d%b%y", utc=True)
        df["T"] = (df["expiry_dt"] - datetime.now(timezone.utc)).dt.total_seconds() / (365.25 * 24 * 3600)
        df["total_variance"] = df["mark_iv"] ** 2 * df["T"]

        df = df[df["T"] > 0] # remove expired options
        df = df[df["open_interest_notional"] >= 10]
        df = df.sort_values(["expiry", "strike"])

        return df

    def get_curve_df(self, df, target_expiry):

        curve_df = df[
            (df["expiry"] == target_expiry) &
            (df["option_type"] == "call")
        ]

        curve_df = curve_df.sort_values("strike")

        return curve_df


class OKX:
    def __init__(self, currencies, target_expiry):
        self.data = {}
         
        for currency in currencies:
            spot, volume = self.get_spot(currency)
            df = self.get_df(currency)
            curve_df = self.get_curve_df(df, target_expiry)

            self.data[currency] = {
                "spot": spot,
                "volume": volume,
                "df": df,
                "curve_df": curve_df
            }

    def get_spot(self, currency):
        url = "https://www.okx.com/api/v5/market/ticker"
        params = {"instId": f"{currency}-USDT"}

        data = requests.get(url=url, params=params).json()["data"][0]

        spot = float(data["last"])
        volume = float(data["vol24h"])
        print("spot:", spot, "volume24h:", volume)

        return spot, volume

    def get_df(self, currency):

        url = f"https://www.okx.com/api/v5/public/instruments?instType=OPTION&uly={currency}-USD"
        data = requests.get(url).json()
        contract_size = float(data["data"][0]["ctVal"])
        
        url = "https://www.okx.com/api/v5/public/open-interest"
        params = {
            "instType": "OPTION",
            "uly": f"{currency}-USD"
        }

        oi = requests.get(url=url, params=params).json()["data"]
        oi_df = pd.DataFrame(oi)
        oi_df = oi_df.rename(columns={"oi": "open_interest"})
        oi_df["open_interest"] = oi_df["open_interest"].astype(float)
        oi_df["open_interest_notional"] = oi_df["open_interest"] * contract_size

        url = "https://www.okx.com/api/v5/public/opt-summary"
        params = {"uly": f"{currency}-USD"}

        summary = requests.get(url=url, params=params).json()["data"]
        summary_df = pd.DataFrame(summary)
        summary_df = summary_df.rename(columns={"markVol": "mark_iv"})
        summary_df["mark_iv"] = summary_df["mark_iv"].astype(float)

        parts = summary_df["instId"].str.split("-")

        summary_df["ccy"] = parts.str[1]
        summary_df["strike"] = parts.str[3].astype(float)
        summary_df["option_type"] = parts.str[4].map({"C": "call", "P": "put"})
        summary_df["expiry"] = pd.to_datetime(parts.str[2], format="%y%m%d").dt.strftime("%Y%m%d")
        summary_df["expiry_dt"] = pd.to_datetime(parts.str[2], format="%y%m%d", utc=True)
        summary_df["T"] = (summary_df["expiry_dt"] - datetime.now(timezone.utc)).dt.total_seconds() / (365.25 * 24 * 3600)
        summary_df["total_variance"] = summary_df["mark_iv"] ** 2 * summary_df["T"]

        df = summary_df.merge(oi_df, on="instId", how="left")

        df = df[df["T"] > 0] # remove expired options
        df = df[df["ccy"] == "USD"]
        df = df[df["open_interest_notional"] >= 10]
        df = df.sort_values(["expiry", "strike"])

        return df

    def get_curve_df(self, df, target_expiry):

        curve_df = df[
            (df["expiry"] == target_expiry) &
            (df["option_type"] == "call")
        ]

        curve_df = curve_df.sort_values("strike")

        return curve_df


class Bybit:
    def __init__(self, currencies, target_expiry):

        self.data = {}
        
        for currency in currencies:
            spot, volume = self.get_spot(currency)
            df = self.get_df(currency)
            curve_df = self.get_curve_df(df, target_expiry)

            self.data[currency] = {
                "spot": spot,
                "volume": volume,
                "df": df,
                "curve_df": curve_df
            }

    def get_spot(self, currency):
        url = "https://api.bybit.com/v5/market/tickers"
        params = {
            "category": "spot",
            "symbol": f"{currency}USDT"
        }

        data = requests.get(url=url, params=params).json()["result"]["list"][0]
        
        spot = float(data["lastPrice"])
        volume = float(data["volume24h"])
        print("spot:", spot, "volume24h:", volume)

        return spot, volume

    def get_df(self, currency):
        contract_size = 1.0

        url = "https://api.bybit.com/v5/market/tickers"
        params = {
            "baseCoin": f"{currency}",
            "category": "option"
        }

        bybit_data = requests.get(url=url, params=params).json()["result"]["list"]
        df = pd.DataFrame(bybit_data)
        df = df.rename(columns={"markIv": "mark_iv", "openInterest": "open_interest"})

        df["open_interest"] = df["open_interest"].astype(float)
        df["open_interest_notional"] = df["open_interest"] * contract_size
        df["mark_iv"] = df["mark_iv"].astype(float)

        parts = df["symbol"].str.split("-")
        df["strike"] = parts.str[2].astype(float)
        df["option_type"] = parts.str[3].map({"C": "call", "P": "put"})
        df["expiry"] = pd.to_datetime(parts.str[1], format="%d%b%y").dt.strftime("%Y%m%d")
        df["expiry_dt"] = pd.to_datetime(parts.str[1], format="%d%b%y", utc=True)
        df["T"] = (df["expiry_dt"] - datetime.now(timezone.utc)).dt.total_seconds() / (365.25 * 24 * 3600)
        df["total_variance"] = df["mark_iv"] ** 2 * df["T"]

        df = df[df["T"] > 0] # remove expired options
        df = df[df["open_interest_notional"] >= 10]
        df = df.sort_values(["expiry", "strike"])

        return df

    def get_curve_df(self, df, target_expiry):

        curve_df = df[
            (df["expiry"] == target_expiry) &
            (df["option_type"] == "call")
        ]

        curve_df = curve_df.sort_values("strike")

        return curve_df