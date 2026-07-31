import requests
# from config import BASE_URL, TOKEN

class TradingDeskAPI:

    # def __init__(self):
    #     self.base_url = BASE_URL
    #     self.headers = {
    #         "Authorization": f"Bearer {TOKEN}"
    #     }

    def get_markets(self):
        r = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={
                "limit": 100,
                "active": "true",
                "closed": "false",
                "order": "liquidity", # sorted by highest liquidity first
                "ascending": "false",
                "liquidity_num_min": 10000,
                "volume_num_min": 5000
            },
            timeout=10
        )
        r.raise_for_status()
        return r.json()

    def get_orderbook(self, token_id):
        url = "https://clob.polymarket.com/book"

        r = requests.get(
            url,
            params={
                "token_id": token_id
            },
            timeout=10
        )

        r.raise_for_status()
        return r.json()