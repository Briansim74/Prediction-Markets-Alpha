import os
import pandas as pd
from datetime import datetime

def save_snapshots(snapshots, counter):

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    DATA_DIR = f"data/{timestamp}"
    
    os.makedirs(DATA_DIR, exist_ok=True)

    filename = f"{DATA_DIR}/yes_markets_{counter:06d}.parquet"

    df = pd.DataFrame(snapshots)

    df.to_parquet(
        filename,
        engine="fastparquet",
        index=False
    )

    print('snapshots saved at: ', filename)