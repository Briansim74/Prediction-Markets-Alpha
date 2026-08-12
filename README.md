# Prediction-Markets-Alpha
Systematic prediction-market trading strategy focused on identifying mispriced event contracts, translating market-implied volatility into probability estimates, and converting probabilistic edge into risk-adjusted trades.

Currently focused on crypto prediction markets, particularly BTC and ETH price-event contracts.

## Core Insight
Prediction-market trading is fundamentally a probability and pricing problem.

The market provides a contract price:
<p align="center"><b>P_market ≈ P(YES)</b></p>

The objective is to determine whether the true probability is sufficiently different from that price to overcome:
- trading fees
- bid/ask spread
- execution costs
- model uncertainty
- position risk
- inventory exposure

The system therefore does not simply ask:
```
"Will Bitcoin go up?"
```
It asks:
```
"Is this contract mispriced relative to my estimate of the probability of the event occurring?"
```
A trade is only attractive when the estimated probability produces sufficient expected value after trading costs.

## System Overview
```
                                       Prediction Markets
                                                │
                                                ▼
                                    Market / Order Book Scan
                                                │                    
                                                ▼                     
                                      Crypto Market Parsing    Options Markets
                                                │                     │
                                                ▼                     ▼
                                        Event Structure           BTC / ETH
                                         Touch / Expiry       Implied Volatility
                                                │                     │
                                                └──────────┬──────────┘
                                                           ▼
                                                     Surface Model
                                                           │
                                                           ▼
                        Portfolio Reconstruction    Probability Model
                                     │                     │
                                     ▼                     ▼
                           Fill / Order Tracking  P(Model) vs P(Market)
                                     │                     │
                                     ▼                     ▼
                              Mark-to-Market        Expected Value
                                     │                     │
                                     ▼                     ▼
                               Equity / P&L          Kelly Sizing
                                     │                     │
                                     └──────────┬──────────┘
                                                ▼
                                        Entry / No Entry
                                                │
                                                ▼
                                         Order Execution
                                                │
                                                ▼
                                      Live Order Management
                                                │
                                                ▼
                                      Inventory / Exit Logic
```
## Edge Decomposition
The strategy's edge can be thought of as:
```
  Probability Model
          +
  Market Mispricing
          -
    Trading Fees
          -
    Execution Costs
          -
     Model Error
          -
   Position Risk
          =
Realizable Trading Edge
```
A probability forecast is only useful if it produces a sufficiently large discrepancy from the executable market price.

Likewise, a large theoretical edge is not useful if it cannot be traded at the quoted price.

## Core Trading Engine
The trading engine consists of five major components:

#### 1. Probability estimation
Estimate the probability of the prediction-market event using external crypto volatility information.

#### 2. Market pricing
Read the current YES/NO order books and determine executable prices.

#### 3. Edge calculation
Compare model probability against executable market prices while incorporating fees.

#### 4. Position sizing
Convert expected value into a position size using fractional Kelly sizing and portfolio limits.

#### 5. Position management
Continuously reassess existing positions and exit when the original trade thesis deteriorates.

## Probability Engine
The system uses external crypto derivatives data to construct a volatility surface for BTC and ETH.

Current market inputs include:
- Deribit
- OKX
- Bybit

The volatility surface is used to estimate the probability distribution of the underlying asset at different strikes and expiries.
```
     BTC / ETH Options
             │
             ▼
   Volatility Observations
             │
             ▼
    Volatility Surface
             │
             ▼
IV at Market Strike / Expiry
             │
             ▼
       Probability Model
             │
             ▼
Prediction Market Fair Value
```

The key idea is that the prediction market and crypto derivatives markets contain different pieces of information.

The prediction market provides the contract price.

The options market provides information about the distribution of future prices.

The trading strategy attempts to exploit discrepancies between the two.

## Event Probability
The system currently distinguishes between two types of crypto prediction contracts.

### Touch Events
Contracts where the underlying asset needs to reach a particular level at any point before expiry. Behaves like a synthetic binary barrier touch option.

Examples:
```
Will BTC reach $120,000 before December 25?
```
or
```
Will ETH fall to $2,500 before expiry?
```
The system estimates:
<p align="center"><b>P(touch)</b></p>

using the underlying price, volatility and time to expiry.

### Expiry Events
Contracts where the underlying must finish above or below a strike at expiry. Behaves like a synthetic European vanilla call/put option.

Examples:
```
Will BTC be above $120,000 on December 25?
```
or
```
Will ETH finish below $3,000?
```
The system estimates:

<p align="center"><b>P(S_T > K)</b></p>

or

<p align="center"><b>P(S_T < K)</b></p>

depending on the contract direction.

## Market Scanner
The scanner continuously filters the available prediction-market universe.

Markets must satisfy:
- acceptingOrders == True
- enableOrderBook == True

The scanner then identifies relevant crypto contracts using keyword matching for:
- Bitcoin
- BTC
- XBT
- Ethereum
- ETH

Each market is parsed into structured trading information:
- currency
- strike
- expiry
- time-to-expiry
- direction
- event type
- YES token
- NO token
- YES bid / ask
- NO bid / ask
- implied volatility
- model probability
- expected value
- Kelly allocation

This converts an unstructured prediction-market question into a tradable quantitative instrument.

## Probability → Price
A prediction-market YES contract can be interpreted approximately as:

<p align="center"><b>P_market = P(YES)</b></p>

For example:
```
YES ask = $0.38
```
implies a market price of approximately:

<p align="center"><b>P(YES) = 38%</b></p>

If the model estimates:
```
P(YES) = 48%
```

there is potentially a meaningful pricing discrepancy.

But the system does not trade on probability difference alone.

It calculates the actual expected value of the executable trade.

## Expected Value
For a YES purchase:
<p align="center"><b>Cost = P_ask + Fee * (P_ask)</b></p>

where:
<p align="center"><b>Fee(P) = fee_rate * P_ask * (1 − P_ask)</b></p>

The expected value is:
<p align="center"><b>EV = P_model * (1 − Cost) − (1 − P_model) * Cost</b></p>

This produces expected P&L per contract.

The same framework is applied to:
- BUY YES
- SELL YES
- BUY NO
- SELL NO

The system therefore evaluates the entire contract from both sides of the book rather than simply comparing model probability with the midpoint.

## Fees Matter
A central feature of the trading system is that expected value is calculated after fees.

A small theoretical probability edge can disappear once transaction costs are included.

For every executable price:
```
    market price
          +
     trading fee
          ↓
 effective entry cost
          ↓
     expected PnL
```

This is important because prediction-market contracts can trade close to fair value while still appearing attractive if fees are ignored.

The strategy therefore treats transaction costs as part of the pricing problem rather than an afterthought.

## Fractional Kelly Position Sizing
Position sizing is based on Kelly sizing with a conservative fractional allocation.

Current configuration:
```
FRACTION = 0.25
```

The system calculates a Kelly allocation from expected value and then uses only a fraction of the theoretical Kelly position.

This provides a balance between:
- maximizing capital efficiency
- controlling model uncertainty
- avoiding excessive concentration
- surviving probability-estimation errors

The resulting allocation is additionally constrained by a maximum position limit.

## Entry Logic
The current strategy requires:
```
ENTRY_EV_THRESHOLD = 0.01
```
meaning the system requires approximately 1% expected value per contract before considering a new position.

Conceptually:
```
  Model Probability
          │
          ▼
Executable Market Price
          │
          ▼
   Fee-Adjusted EV
          │
          ▼
EV > Entry Threshold?
         / \
       NO   YES
       │     │
     SKIP   TRADE
```
This prevents the system from deploying capital into marginal opportunities.

## Portfolio Constraints
The current configuration uses:
```
MAX_POSITION = 0.05
```
so an individual position is capped at approximately:
```
5% of available capital
```
The actual allocation is:
<p align="center"><b>min(cash * fractional Kelly, cash * maximum position)</b></p>

This means even a highly attractive model signal cannot automatically consume a disproportionate amount of portfolio capital.

## Execution
Once a trade passes the EV and risk checks, the system converts the desired dollar allocation into contracts:
```
     Dollar Allocation
             │
             ▼
      Contract Price
             │
             ▼
      Number of Shares
             │
             ▼
Exchange Size Normalization
             │
             ▼
     Tick-Size Rounding
             │
             ▼
        Limit Order
```

Orders are currently submitted as:
- BUY
- GTC
- LIMIT

The system also records the resulting order ID and maintains an internal order ledger.

## Order Management
Open orders are continuously reconciled against the trading API.

The system tracks:
- order_id
- condition_id
- token_id
- side
- price
- requested size
- filled size
- remaining size
- status
- created_at
- cancelled_at

This allows the internal trading state to remain synchronized with exchange state.

Orders can subsequently be evaluated for cancellation when:
- expected value disappears
- market price changes
- market conditions change
- inventory constraints change
- the order becomes stale

The objective is to avoid leaving capital committed to trades whose original edge no longer exists.

## Inventory Management
Once a position exists, the system stops treating it as a new opportunity.

It becomes an inventory management problem.

For every open position:
```
Current Position
       │
       ▼
Current Market Price
       │
       ▼
Recalculate Probability
       │
       ▼
Recalculate Expected Value
       │
       ▼
Hold or Exit
```

Current exit threshold:
```
EXIT_EV_THRESHOLD = -0.02
```
If the expected value of continuing to hold the position deteriorates sufficiently, the system generates an exit signal.

## Exit Logic
The system evaluates existing inventory using the current bid.

For a YES position:
- current YES bid
- buy YES EV
- sell YES EV

For a NO position:
- current NO bid
- buy NO EV
- sell NO EV

The important distinction is that holding a position has an opportunity cost.

The relevant question is not:
```
"Did I buy this at a good price?"
```
It is:
```
"Given today's information, is holding this position still better than exiting?"
```
This allows the portfolio to dynamically recycle capital into better opportunities.

## FIFO Portfolio Reconstruction
Executed trades are stored as fills rather than treating API positions as the complete source of truth.

The system reconstructs positions from the historical fill ledger.
```
       Trades
          │
          ▼
Chronological Ordering
          │
          ▼
   FIFO Matching
          │
          ├── Open Lots
          │
          └── Closed Lots
                  │
                  ▼
              Realized P&L
```
For every token, BUY lots are maintained in a FIFO queue.

SELL executions consume those lots sequentially.

This produces:
- current position
- cost basis
- average entry
- realized P&L
- realized shares
- realized fees

This makes portfolio accounting independent of the current exchange position snapshot.

## Mark-to-Market
Open positions are continuously marked against current executable bids.

For each position:
<p align="center"><b>Market_Value = Shares * Current_Bid</b></p>

and:
<p align="center"><b>Unrealized_PnL = Market_Value − Cost_Basis</b></p>

The portfolio therefore maintains both:
<p align="center"><b>Realized_PnL + Unrealized_PnL</b></p>

rather than relying only on settled trades.

## Equity Engine
Portfolio equity is calculated as:
<p align="center"><b>Equity = Cash + Market_Value</b></p>

The system records periodic equity snapshots containing:
- timestamp
- cash
- market value
- equity
- realized PnL
- unrealized PnL
- daily return

This creates a persistent performance history for evaluating the trading strategy.

## Complete Trading Loop
The live trading workflow is:
```
1. Load Portfolio State
        │
2. Build Volatility Surface
        │
3. Pull Prediction Markets
        │
4. Scan Crypto Contracts
        │
5. Pull Order Books
        │
6. Estimate IV
        │
7. Estimate Event Probability
        │
8. Calculate Fee-Adjusted EV
        │
9. Synchronize Fills
        │
10. Reconstruct Portfolio
        │
11. Synchronize Orders
        │
12. Manage Existing Orders
        │
13. Mark Positions to Market
        │
14. Calculate Equity
        │
15. Manage Existing Inventory
        │
16. Find New Opportunities
        │
17. Submit Orders
        │
18. Persist Trading State
        │
19. Repeat
```
The system therefore operates as a closed-loop trading process rather than a standalone prediction model.

## Project Structure
```
prediction-markets-arb/
│
├── scanner/
│   ├── scanner.py
│   ├── options.py
│
├── execution/
│   ├── api_client.py
│
├── data/
│   ├── markets.parquet
│   ├── opportunities.parquet
│   ├── orders.parquet
│   ├── fills.parquet
│   ├── positions.parquet
│   ├── realized_pnl.parquet
│   └── equity.parquet
│
└── market.ipynb
```

## Trading State
The system persists the trading state between iterations.
```
Markets
   │
   ├── markets.parquet
   └── opportunities.parquet
           
Orders
   │
   └── orders.parquet

Fills
   │
   └── fills.parquet

Portfolio
   │
   ├── positions.parquet
   └── realized_pnl.parquet

Performance
   │
   └── equity.parquet
```
This allows for the opportunity to restart the engine without losing its internal portfolio history.

## Design Principles
### Probability first
The system attempts to quantify the probability of the event rather than trade purely on market momentum.

#### Trade the discrepancy
The objective is not to predict the market in isolation.

It is to identify:
<p align="center"><b>P_model ≠ P_market</b></p>

with enough margin to justify taking risk.

#### Price execution matters
The strategy evaluates executable bid/ask prices rather than relying exclusively on midpoints.

#### Fees are part of the signal
An edge that disappears after fees is not an edge.

#### Position sizing matters
A good forecast with excessive sizing can still produce a bad trading strategy.

#### Inventory is dynamic
Existing positions are continuously reevaluated as market prices, volatility and time-to-expiry change.

#### Capital should move toward the best opportunities
The portfolio is treated as a dynamic allocation problem rather than a collection of independent bets.

## What This System Is
This project is a quantitative prediction-market trading engine.

It combines:
```
Options Market Data
        +
Volatility Modeling
        +
Probability Estimation
        +
Prediction Market Pricing
        +
Expected Value
        +
Fractional Kelly
        +
Execution
        +
Inventory Management
        +
Portfolio Accounting
```
The goal is to turn differences between model-implied probability and prediction-market pricing into systematically managed trades.
