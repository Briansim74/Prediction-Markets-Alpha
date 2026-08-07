import requests
import uuid
from decimal import Decimal

# from config import BASE_URL, TOKEN


# import os

# BASE_URL = os.getenv(
#     "API_URL",
#     "https://alphasignal-dev.moretoncp.com"
# )

# TOKEN = os.getenv("API_TOKEN")

# Later you set:
# export API_TOKEN="your_jwt_here"

# EXAMPLE
# api = TradingDeskAPI(
#     "https://alphasignal-dev.moretoncp.com",
#     TOKEN
# )

class TradingDeskAPI:

    # def __init__(self, base_url, token):
    #     self.base_url = base_url.rstrip("/")
    #     self.session = requests.Session()

    #     self.session.headers.update({
    #         "Authorization": f"Bearer {token}",
    #         "Content-Type": "application/json"
    #     })

    def get_markets(self):

        url = "https://gamma-api.polymarket.com/markets"
        params = {
                "limit": 10000,
                "active": "true",
                "closed": "false",
                "order": "liquidity", # sorted by highest liquidity first
                "ascending": "false",
                "liquidity_num_min": 10000,
                "volume_num_min": 5000
            }

        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()

        return r.json()

    def get_orderbook(self, token_id):

        url = "https://clob.polymarket.com/book"
        params={"token_id": token_id}

        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()

        return r.json()

    def _get(self, path, params=None):
        r = self.session.get(
            self.base_url + path,
            params=params,
            timeout=10
        )
        r.raise_for_status()
        return r.json()


    def _post(self, path, data=None):
        r = self.session.post(
            self.base_url + path,
            json=data,
            timeout=10
        )
        r.raise_for_status()
        return r.json()


    def _delete(self, path):
        r = self.session.delete(
            self.base_url + path,
            timeout=10
        )
        r.raise_for_status()
        return r.json()

    # order = api.place_limit(
    #     token_id="123456",
    #     side="buy",
    #     price=0.55,
    #     size=10
    # )

    def place_limit(
                    self,
                    token_id,
                    side,
                    price,
                    size
            ):

        self.validate_limit_order(
            token_id,
            price,
            size
        )

        payload = {
            "token_id": token_id,
            "side": side,
            "price": str(price),
            "size": str(size),
            "order_type": "GTC",
            "client_order_id": str(uuid.uuid4())
        }


        return self._post(
            "/v1/orders",
            payload
        )

    def get_orders(self, market=None):

        params={}

        if market:
            params["market"] = market

        return self._get(
            "/v1/orders",
            params
        )

    def cancel_order(self, order_id):

        return self._delete(
            f"/v1/orders/{order_id}"
        )

    def cancel_all(self):

        return self._post(
            "/v1/orders/cancel-all"
        )

    def market_buy(
            self,
            token_id,
            amount
    ):

        payload = {
            "token_id": token_id,
            "side":"buy",
            "amount":str(amount),
            "order_type":"FOK"
        }


        return self._post(
            "/v1/orders/market",
            payload
        )

    def get_balance(self):

        return self._get(
            "/v1/balance"
        )

    def get_trades(self, token_id=None):

        params={}

        if token_id:
            params["asset_id"]=token_id


        return self._get(
            "/v1/trades",
            params
        )

    def get_conditional_balance(self, token_id):

        return self._get(
            "/v1/balance",
            params={
                "asset_type": "conditional",
                "token_id": token_id
            }
        )

    def get_positions(self, markets):

        positions = []

        for market in markets:

            for token in market["tokens"]:

                token_id = token["token_id"]

                balance = self.get_balance(
                    asset_type="conditional",
                    token_id=token_id
                )

                shares = float(balance["balance"])

                if shares > 0:

                    positions.append({
                        "condition_id": market["conditionId"],
                        "token_id": token_id,
                        "outcome": token["outcome"],
                        "shares": shares
                    })

        return positions


    def get_position(
            self,
            yes_token,
            no_token
    ):

        yes = self.get_conditional_balance(
            yes_token
        )

        no = self.get_conditional_balance(
            no_token
        )


        return {
            "YES": float(yes["balance"]),
            "NO": float(no["balance"])
        }

    def get_position(self, token_id):

        trades = self.get_trades(token_id)

        shares = 0
        cost = 0


        for t in trades["trades"]:

            size=float(t["size"])
            price=float(t["price"])


            if t["side"]=="BUY":
                shares += size
                cost += size*price

            else:
                shares -= size
                cost -= size*price


        avg_price = (
            cost/shares
            if shares > 0
            else 0
        )


        return {
            "token_id": token_id,
            "shares": shares,
            "average_price": avg_price,
            "cost_basis": cost
        }

    def get_tick_size(self, token_id):

        return self._get(
            f"/v1/markets/tick-size/{token_id}"
        )

    def round_to_tick(price, tick):

        price = Decimal(str(price))
        tick = Decimal(str(tick))

        return (
            price // tick
        ) * tick

    def normalize_size(self, size):

        return round(float(size),6)



    def validate_order(price, size):

        MIN_NOTIONAL = 1.0

        if price * size < MIN_NOTIONAL:
            raise ValueError(
                f"Order too small: ${price*size:.2f}"
            )

    def validate_limit_order(
            self,
            token_id,
            price,
            size
    ):

        tick_info = self.get_tick_size(token_id)

        tick = Decimal(
            tick_info["minimum_tick_size"]
        )

        price = Decimal(str(price))
        size = Decimal(str(size))


        # price range

        if price <= 0 or price >= 1:
            raise ValueError(
                "Price must be between 0 and 1"
            )


        # tick check

        if price % tick != 0:
            raise ValueError(
                f"Price must be multiple of {tick}"
            )


        # size precision

        if len(
            str(size).split(".")[-1]
        ) > 6:
            raise ValueError(
                "Size max 6 decimals"
            )


        # minimum order value

        if price * size < Decimal("1"):
            raise ValueError(
                "Minimum order notional is ~$1"
            )