import os
import glob
import uuid
import requests
import pandas as pd
from decimal import Decimal

class TradingDeskAPI:

    def __init__(self, base_url, email, password, token):
        self.base_url = base_url
        self.email = email
        self.password = password

        self.session = requests.Session()
        self.token = token

        self.session.headers.update({
            "Content-Type": "application/json"
        })

        # If a token was provided when creating the class,
        # immediately authenticate the session with it.
        if self.token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.token}"
            })

    # # -----------------------------------------------------
    # # Generic request helper
    # # -----------------------------------------------------

    def request(self, method, path, **kwargs):
        response = self.session.request(method,
            f"{self.base_url}{path}",
            **kwargs,
        )

        if not response.ok:
            print("STATUS:", response.status_code)
            print("RESPONSE:", response.text)
            print("REQUEST URL:", response.url)
            print("REQUEST BODY:", kwargs.get("json"))
            print("REQUEST PARAMS:", kwargs.get("params"))

        response.raise_for_status()
        return response.json()

    # # -----------------------------------------------------
    # # Non authenticated functions
    # # -----------------------------------------------------
    def get_all_markets(self, DATA_DIR, count_limit=20, liquidity_num_min=10000, volume_num_min=5000):

        url = "https://gamma-api.polymarket.com/markets/keyset"

        after_cursor = None
        total_markets = 0

        for count in range(count_limit):
            params = {
                "limit": 500,
                "active": "true",
                "closed": "false",
                "liquidity_num_min": liquidity_num_min,
                "volume_num_min": volume_num_min,
                "include_tag": "true",
            }

            if after_cursor:
                params["after_cursor"] = after_cursor

            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()

            data = r.json()

            # print(data)
            markets = data.get("markets", [])

            if not markets:
                break

            # Convert ONLY this batch to DataFrame and save immediately
            df = pd.DataFrame(markets)
            filename = f"{DATA_DIR}/markets_{count:05d}.parquet"
            df.to_parquet(filename, index=False)

            total_markets += len(markets)

            print(f"Fetched {len(markets):,} markets | Total: {total_markets:,}")

            after_cursor = data.get("next_cursor")

            if not after_cursor:
                break

        parquet_files = glob.glob(f"{DATA_DIR}/markets_*.parquet")

        all_markets_df = pd.concat([pd.read_parquet(f) for f in parquet_files], ignore_index=True)

        # Delete parquet files after successful concat
        for f in parquet_files:
            os.remove(f)

        return all_markets_df

    # def get_markets(self, limit=10000, liquidity_num_min=10000, volume_num_min=5000):

    #     url = "https://gamma-api.polymarket.com/markets"
    #     params = {
    #             "limit": limit,
    #             "active": "true",
    #             "closed": "false",
    #             "order": "liquidity", # sorted by highest liquidity first
    #             "ascending": "false",
    #             "liquidity_num_min": liquidity_num_min,
    #             "volume_num_min": volume_num_min,
    #             "include_tag": "true",
    #         }

    #     r = requests.get(url, params=params, timeout=10)
    #     r.raise_for_status()

    #     return r.json()

    # def get_orderbook(self, token_id):

    #     url = "https://clob.polymarket.com/book"
    #     params = {"token_id": token_id}

    #     r = requests.get(url, params=params, timeout=10)
    #     r.raise_for_status()

    #     return r.json()

    # =====================================================
    # LIMIT ORDERS - TEST
    # =====================================================

    def place_limit_order_test(
            self,
            token_id,
            side,
            price,
            size,
            order_type="GTC",
            client_order_id=None,
            expiration=None,
        ):
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())

        print("{ TEST } - PLACED LIMIT ORDER")

        return {
            "clob_order_id": str(uuid.uuid4()),
            "replayed": False
        }

    # -----------------------------------------------------
    # Quantity Normalization
    # -----------------------------------------------------

    def round_to_tick(self, price, tick):

        price = Decimal(str(price))
        tick = Decimal(str(tick))

        return (price // tick) * tick

    def normalize_size(self, size):

        return round(float(size), 6)

    # -----------------------------------------------------
    # 1. Register
    # -----------------------------------------------------

    def register(self):
        data = {
            "email": self.email,
            "password": self.password,
        }

        response = self.session.post(
            f"{self.base_url}/v1/auth/register",
            json=data,
        )

        response.raise_for_status()

        result = response.json()

        if result.get("access_token"):
            self.token = result["access_token"]

        return result

    # -----------------------------------------------------
    # 2. Get current user
    # -----------------------------------------------------

    def get_me(self):
        result = self.request(
            "GET",
            "/v1/users/me",
        )

        self.user_id = result.get("id")

        return result

    # -----------------------------------------------------
    # 3. Login
    # -----------------------------------------------------

    def login(self):
        data = {
            "email": self.email,
            "password": self.password,
        }

        response = self.session.post(
            f"{self.base_url}/v1/auth/login",
            json=data,
        )

        response.raise_for_status()

        result = response.json()

        self.token = result["access_token"]

        # IMPORTANT:
        # Update the session with the newly received JWT
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}"
        })

        return result

    # -----------------------------------------------------
    # 4. Health
    # -----------------------------------------------------

    def health(self):
        return self.request(
            "GET",
            "/health",
        )

    # =====================================================
    # MARKETS
    # =====================================================

    # -----------------------------------------------------
    # 5. List markets
    # -----------------------------------------------------

    def list_markets(
            self,
            limit=500,
            keyword=None,
        ):
        params = {
            "limit": limit,
            "active": "true",
            "closed": "false",
        }

        if keyword:
            params["keyword"] = keyword

        return self.request(
            "GET",
            "/v1/markets",
            params=params,
        )["markets"]

    # -----------------------------------------------------
    # 6. Order book
    # -----------------------------------------------------

    def orderbook(self, token_id):
        return self.request(
            "GET",
            f"/v1/markets/orderbook/{token_id}",
        )

    # -----------------------------------------------------
    # 7. Price estimate
    # -----------------------------------------------------

    def price_estimate(
        self,
        token_id,
        side="buy",
        amount=1,
        order_type="FOK",
    ):
        params = {
            "side": side,
            "amount": amount,
            "order_type": order_type,
        }

        return self.request(
            "GET",
            f"/v1/markets/price-estimate/{token_id}",
            params=params,
        )

    # -----------------------------------------------------
    # 8. Tick size
    # -----------------------------------------------------

    def tick_size(self, token_id):
        return self.request(
            "GET",
            f"/v1/markets/tick-size/{token_id}",
        )

    # -----------------------------------------------------
    # 9. Fee rate
    # -----------------------------------------------------

    def fee_rate(self, token_id):
        return self.request(
            "GET",
            f"/v1/markets/fee-rate/{token_id}",
        )

    # -----------------------------------------------------
    # 10. Last trade price
    # -----------------------------------------------------

    def last_trade_price(self, token_id):
        return self.request(
            "GET",
            f"/v1/markets/last-trade-price/{token_id}",
        )

    # -----------------------------------------------------
    # 11. CLOB market info
    # -----------------------------------------------------

    def clob_info(self, condition_id):
        return self.request(
            "GET",
            f"/v1/markets/clob-info/{condition_id}",
        )

    # -----------------------------------------------------
    # 12. Market detail
    # -----------------------------------------------------

    def market_detail(self, condition_id):
        return self.request(
            "GET",
            f"/v1/markets/detail/{condition_id}",
        )

    # =====================================================
    # LIMIT ORDERS
    # =====================================================

    # -----------------------------------------------------
    # 13. Place limit order
    # -----------------------------------------------------

    def place_limit_order(
        self,
        token_id,
        side,
        price,
        size,
        order_type="GTC",
        client_order_id=None,
        expiration=None,
    ):
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())

        data = {
            "token_id": token_id,
            "side": side,
            "price": str(price),
            "size": str(size),
            "order_type": order_type,
            "client_order_id": client_order_id,
        }

        if expiration is not None:
            data["expiration"] = expiration

        return self.request(
            "POST",
            "/v1/orders",
            json=data,
        )

    #  returns:   {
    # "clob_order_id": "...",
    # "replayed": false
    # }

    # -----------------------------------------------------
    # 14. List open orders
    # -----------------------------------------------------

    def list_orders(
        self,
        market=None,
        asset_id=None,
    ):
        params = {}

        if market:
            params["market"] = market

        if asset_id:
            params["asset_id"] = asset_id

        return self.request(
            "GET",
            "/v1/orders",
            params=params,
        )

    # -----------------------------------------------------
    # 15. Get order
    # -----------------------------------------------------

    def get_order(self, clob_order_id):
        return self.request(
            "GET",
            f"/v1/orders/{clob_order_id}",
        )

    # -----------------------------------------------------
    # 16. Cancel order
    # -----------------------------------------------------

    def cancel_order(self, clob_order_id):
        return self.request(
            "DELETE",
            f"/v1/orders/{clob_order_id}",
        )

    # =====================================================
    # MARKET ORDERS
    # =====================================================

    # -----------------------------------------------------
    # 17. Market order
    # -----------------------------------------------------

    # def market_order(
    #     self,
    #     token_id,
    #     side,
    #     amount,
    #     order_type="FOK",
    #     client_order_id=None,
    # ):
    #     data = {
    #         "token_id": token_id,
    #         "side": side,
    #         "amount": str(amount),
    #         "order_type": order_type,
    #     }

    #     if client_order_id:
    #         data["client_order_id"] = client_order_id

    #     return self.request(
    #         "POST",
    #         "/v1/orders/market",
    #         json=data,
    #     )

    # =====================================================
    # BULK CANCELS
    # =====================================================

    # -----------------------------------------------------
    # 18. Cancel batch
    # -----------------------------------------------------

    def cancel_batch(self, order_ids):
        return self.request(
            "POST",
            "/v1/orders/cancel-batch",
            json={
                "order_ids": order_ids
            },
        )

    # -----------------------------------------------------
    # 19. Cancel all
    # -----------------------------------------------------

    def cancel_all(self):
        return self.request(
            "POST",
            "/v1/orders/cancel-all",
        )

    # -----------------------------------------------------
    # 20. Cancel market
    # -----------------------------------------------------

    def cancel_market(
        self,
        condition_id,
        asset_id=None,
    ):
        data = {
            "market": condition_id
        }

        if asset_id:
            data["asset_id"] = asset_id

        return self.request(
            "POST",
            "/v1/orders/cancel-market",
            json=data,
        )

    # =====================================================
    # TRADES & BALANCE
    # =====================================================

    # -----------------------------------------------------
    # 21. Trade history
    # -----------------------------------------------------

    def trades(
        self,
        market=None,
        asset_id=None,
        before=None,
        after=None,
        next_cursor=None,
    ):
        params = {}

        if market:
            params["market"] = market

        if asset_id:
            params["asset_id"] = asset_id

        if before:
            params["before"] = before

        if after:
            params["after"] = after

        if next_cursor:
            params["next_cursor"] = next_cursor

        return self.request(
            "GET",
            "/v1/trades",
            params=params,
        )

    # -----------------------------------------------------
    # 22. Balance
    # -----------------------------------------------------

    def balance(
        self,
        asset_type="collateral",
        token_id=None,
    ):
        params = {
            "asset_type": asset_type,
        }

        if token_id:
            params["token_id"] = token_id

        return self.request(
            "GET",
            "/v1/balance",
            params=params,
        )

    # -----------------------------------------------------
    # 23. Sync balance
    # -----------------------------------------------------

    def sync_balance(
        self,
        asset_type="collateral",
        token_id=None,
    ):
        params = {
            "asset_type": asset_type,
        }

        if token_id:
            params["token_id"] = token_id

        return self.request(
            "POST",
            "/v1/balance/sync",
            params=params,
        )

    # -----------------------------------------------------
    # 24. Logout
    # -----------------------------------------------------

    def logout(self):
        result = self.request(
            "POST",
            "/v1/auth/logout",
        )

        self.token = None

        return result

    # -----------------------------------------------------
    # MISC
    # -----------------------------------------------------

    def get_all_tags(self):
        url = "https://gamma-api.polymarket.com/tags"

        all_tags = []
        offset = 0
        limit = 100

        while True:
            params = {
                "limit": limit,
                "offset": offset,
            }

            r = requests.get(
                url,
                params=params,
                timeout=30,
            )
            r.raise_for_status()

            tags = r.json()

            if not tags:
                break

            all_tags.extend(tags)

            # print(
            #     f"Fetched {len(tags)} tags | "
            #     f"Total: {len(all_tags)}"
            # )

            if len(tags) < limit:
                break

            offset += limit

        return all_tags

    def get_crypto_tags(self, all_tags):

        CRYPTO_TAGS = {
            "crypto",
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "solana",
            "dogecoin",
            "doge",
            "xrp",
            "ripple",
            "cardano",
            "ada",
            "avalanche",
            "avax",
            "chainlink",
            "link",
            "polygon",
            "matic",
            "bnb",
            "binance",
            "altcoin",
            "altcoins",
            "defi",
            "nft",
            "nfts",
            "stablecoin",
            "stablecoins",
            "memecoin",
            "memecoins",
            "cryptopunks",
        }

        all_tags = self.get_all_tags()

        crypto_tags = [tag for tag in all_tags if(
                tag.get("label", "").lower() in CRYPTO_TAGS
                or tag.get("slug", "").lower() in CRYPTO_TAGS
            )]

        for tag in crypto_tags:
            print(
                tag["id"],
                tag["label"],
                tag["slug"],
            )

        crypto_tag_ids = [tag["id"] for tag in crypto_tags]

        return crypto_tag_ids