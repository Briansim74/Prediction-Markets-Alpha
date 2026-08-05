import requests

# from config import BASE_URL, TOKEN


# import os

# BASE_URL = os.getenv(
#     "API_URL",
#     "https://alphasignal-dev.moretoncp.com"
# )

# TOKEN = os.getenv("API_TOKEN")

# Later you set:
# export API_TOKEN="your_jwt_here"

class TradingDeskAPI:

    # def __init__(self):
    #     self.base_url = BASE_URL
    #     self.headers = {
    #         "Authorization": f"Bearer {TOKEN}"
    #     }

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