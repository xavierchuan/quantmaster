import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.append(REPO_ROOT)

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
from datetime import datetime
import pandas as pd

from shared.utils.config import OANDA_TOKEN, OANDA_ACCOUNT_ID

client = oandapyV20.API(access_token=OANDA_TOKEN)

params = {
    "count": 5,
    "granularity": "H1",  # 1小时K线
    "price": "M"
}

r = instruments.InstrumentsCandles(instrument="EUR_USD", params=params)
client.request(r)

candles = r.response['candles']
for c in candles:
    print(c)
