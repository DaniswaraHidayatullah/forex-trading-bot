# Nanda Method V2: Rejection (paper)

The two independent Standard USD ledgers each start at $100, have no top-ups,
use fixed 0.10 and 0.05 standard lots respectively, and have no $3 risk filter.
Unlimited eligibility is assumed; required margin is approximated as zero below
$5000, with finite equity tiers thereafter. Historical news/session HMR, swap,
slippage and Stop Out Protection are not simulated. A failed account stays
failed. Stop-out threshold is zero equity. Do not read these balances as fills.

Reuse V1's H1 downtrend and first M15 retest of broken support. Require the
retest candle to close below its open and the level. Sell at the next contiguous
M15 open only if still below support. Distance = max(5.00, retest high + 0.40
minus entry); TP = 2 distances. Price units, not pip counts. Twelve Data OHLC
is a bid proxy, spread fixed at 0.30. A running candle contributes its open
only, never its high/low/close. One open position per ledger.

Signals and account trades share IDs in signals/nanda_v2_trial.json. For
strategy WR, ambiguous bars, data gaps and 48h timeouts are excluded. For
account balances they are charged one planned loss limited by account equity,
explicitly counted as assumed losses. These are not proven outcomes. Fresh
paper signals use the signal channel; the 30-day report goes to bot-report
after open positions finish. NANDA_V2_ENABLED=false disables V2 independently.
