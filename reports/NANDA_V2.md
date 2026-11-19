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

Discord now has two stages. A watch is emitted only after a closed breakdown
and before the first touch visible on the M1 proxy. The confirmation requires
a closed bearish retest; both kinds require age under 15 minutes and continuous
M1 snapshots through the current UTC minute. The partial M1 candle is only a
notification veto, never a strategy input. Missing data/quota, old alerts or an
already hit exit do not create an entry message. This may produce no alerts if
Actions is late. At most one extra Twelve Data M1 fetch per run with candidates.
Journal alerts record the Discord HTTP acknowledgment time (not proof of receipt
on a phone), feed observation and signal ID. No additional lots or mentions.

Compare alerted trades separately, without changing the journal:
`python tools/eval_nanda_notifications.py signals/nanda_v2_trial.json exness_ticks.csv`.
Use a chronological CSV with columns timestamp (offset required), symbol
(XAUUSDm), bid, ask. Exness ticks are not provided by the scheduled runner.
The first bid at/after acknowledgment within five seconds is the modeled SELL
entry; the first subsequent ask at TP/SL is the modeled exit, not a broker fill.
Tick gaps over 60 seconds and missing coverage stay unscored. The JSON output
separates all V2, notified V2 baseline OHLC and post-notification tick results.
Do not compare resolved-only win rates without also comparing coverage/counts.
