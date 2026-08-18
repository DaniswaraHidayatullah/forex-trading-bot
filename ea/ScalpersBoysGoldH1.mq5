//+------------------------------------------------------------------+
//|                                       ScalpersBoysGoldH1.mq5      |
//|   EA GOLD H1 — TREND-PULLBACK (bukan mean-rev!).                  |
//|                                                                  |
//|   >>> Di H1, tren gold BERSIH -> ikut tren works (di M15 gagal). <<<|
//|     * Tren: EMA50 vs EMA200 (H1).                                 |
//|     * Entry: RSI nyilang 50 SEARAH tren (pullback kelar,          |
//|       momentum lanjut) — BUKAN fade ekstrem.                      |
//|     * SL = ATR(H1) x 1.2, RR 1:2 (default).                       |
//|     * AUTO-LOT risiko% (sama kayak EA BTC).                       |
//|     * Maks 2 posisi, sesi 05-21 GMT + weekend guard (gold).       |
//|                                                                  |
//|   Backtest H1 (51 hari, spread 0.30): WR 42%, eksp +0.25R,        |
//|   LOLOS IS/OOS (+0.08/+0.40). Replikasi temuan lama "PULL52"      |
//|   (IS/OOS +0.32/+0.29) -> kredibel, bukan sekali-muncul.          |
//|   CAVEAT: 40 trade = kecil, WAJIB forward-test dulu.              |
//|                                                                  |
//|   Indikator dibaca bar TERTUTUP (shift 1) -> anti look-ahead.     |
//|   DEMO dulu. Magic beda dari gold v6 & BTC (nggak nabrak).        |
//+------------------------------------------------------------------+
#property copyright "Scalper's Boys"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//--- Input ----------------------------------------------------------
input bool   InpUseAutoLot   = true;    // ON=hitung lot dari risiko%
input double InpRiskPct      = 2.0;     // risiko per trade (% saldo)
input double InpLot          = 0.20;    // lot tetap (kalau auto-lot OFF)
input double InpRR           = 2.0;     // Reward:Risk (RR2: OOS +0.40; RR1.5: WR 50% lebih tinggi)
input double InpAtrMult      = 1.2;     // SL = ATR(H1) x ini
input int    InpAtrPeriod    = 14;      // periode ATR (H1)
input int    InpRsiPeriod    = 14;      // periode RSI (H1)
input double InpRsiMid       = 50.0;    // level silang RSI (pullback) — searah tren
input int    InpEmaTrendF    = 50;      // EMA tren cepat (H1)
input int    InpEmaTrendS    = 200;     // EMA tren lambat (H1)
input int    InpMaxPositions = 2;       // maks posisi barengan
//--- jam & berita (gold: sesi + weekend guard) ----------------------
input bool   InpUseSession   = true;    // gold: batasi jam
input int    InpSessStart    = 5;       // jam GMT mulai
input int    InpSessEnd      = 21;      // jam GMT selesai
input bool   InpWeekendGuard = true;    // gold tutup weekend
input bool   InpNewsFilter   = true;    // stop entry sekitar berita high-impact USD
input int    InpNewsBeforeMin = 30;     // menit SEBELUM berita
input int    InpNewsAfterMin  = 30;     // menit SETELAH berita
input string InpNewsCurrency = "USD";   // mata uang berita dipantau
input long   InpMagic        = 20260818;// pembeda posisi (BEDA dari gold v6 & BTC!)

//--- Handle indikator (SEMUA di H1) ---------------------------------
int      hRsi, hAtr, hEmaF, hEmaS;
datetime lastBar = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   hRsi  = iRSI(_Symbol, PERIOD_H1, InpRsiPeriod, PRICE_CLOSE);
   hAtr  = iATR(_Symbol, PERIOD_H1, InpAtrPeriod);
   hEmaF = iMA(_Symbol, PERIOD_H1, InpEmaTrendF, 0, MODE_EMA, PRICE_CLOSE);
   hEmaS = iMA(_Symbol, PERIOD_H1, InpEmaTrendS, 0, MODE_EMA, PRICE_CLOSE);
   if(hRsi==INVALID_HANDLE || hAtr==INVALID_HANDLE ||
      hEmaF==INVALID_HANDLE || hEmaS==INVALID_HANDLE)
   {
      Print("Gagal membuat handle indikator");
      return(INIT_FAILED);
   }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(30);
   PrintFormat("ScalpersBoysGoldH1 aktif di %s — %s RR 1:%.1f trend-pullback EMA%d/%d (H1)",
               _Symbol, InpUseAutoLot?"auto-lot "+DoubleToString(InpRiskPct,1)+"%":"lot "+DoubleToString(InpLot,2),
               InpRR, InpEmaTrendF, InpEmaTrendS);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hRsi);  IndicatorRelease(hAtr);
   IndicatorRelease(hEmaF); IndicatorRelease(hEmaS);
}

double Val(int handle, int shift)
{
   double b[];
   if(CopyBuffer(handle, 0, shift, 1, b) <= 0) return(EMPTY_VALUE);
   return(b[0]);
}

bool NewH1Bar()
{
   datetime t = iTime(_Symbol, PERIOD_H1, 0);
   if(t == lastBar) return(false);
   lastBar = t;
   return(true);
}

int CountMyPositions()
{
   int n = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(PositionGetTicket(i) == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic) n++;
   }
   return(n);
}

bool InSession()
{
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   if(InpWeekendGuard)
   {
      if(dt.day_of_week == 6) return(false);
      if(dt.day_of_week == 5 && dt.hour >= 21) return(false);
      if(dt.day_of_week == 0 && dt.hour < 22) return(false);
   }
   if(InpUseSession)
      if(dt.hour < InpSessStart || dt.hour >= InpSessEnd) return(false);
   return(true);
}

bool IsNewsBlackout()
{
   if(!InpNewsFilter) return(false);
   datetime now = TimeCurrent();
   MqlCalendarValue values[];
   int total = CalendarValueHistory(values, now - InpNewsAfterMin*60,
                                    now + InpNewsBeforeMin*60, NULL, InpNewsCurrency);
   for(int i = 0; i < total; i++)
   {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev)) continue;
      if(ev.importance == CALENDAR_IMPORTANCE_HIGH) return(true);
   }
   return(false);
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!NewH1Bar()) return;                  // evaluasi 1x per bar H1
   if(!InSession()) return;
   if(IsNewsBlackout()) return;
   if(CountMyPositions() >= InpMaxPositions) return;

   // indikator bar TERTUTUP: rsi shift1 & shift2 (deteksi silang), ema shift1
   double rsi1 = Val(hRsi,1), rsi2 = Val(hRsi,2);
   double atr  = Val(hAtr,1);
   double emaF = Val(hEmaF,1), emaS = Val(hEmaS,1);
   if(rsi1==EMPTY_VALUE || rsi2==EMPTY_VALUE || atr==EMPTY_VALUE || atr<=0 ||
      emaF==EMPTY_VALUE || emaS==EMPTY_VALUE) return;

   int trend = (emaF > emaS) ? 1 : ((emaF < emaS) ? -1 : 0);
   if(trend == 0) return;

   // PULLBACK: RSI nyilang InpRsiMid searah tren
   bool crossUp = (rsi1 >= InpRsiMid && rsi2 < InpRsiMid);
   bool crossDn = (rsi1 <= InpRsiMid && rsi2 > InpRsiMid);
   bool wantBuy  = (trend == 1  && crossUp);
   bool wantSell = (trend == -1 && crossDn);
   if(!wantBuy && !wantSell) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double price = wantBuy ? ask : bid;

   double slDist = atr * InpAtrMult;
   double point  = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minDist = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   if(slDist < minDist*1.2) slDist = minDist*1.2;
   double tpDist = slDist * InpRR;

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double lot    = CalcLot(slDist);
   if(lot <= 0) { Print("Lot 0 — cek saldo/kontrak"); return; }

   double sl, tp;
   bool ok;
   if(wantBuy)
   {
      sl = NormalizeDouble(price - slDist, digits);
      tp = NormalizeDouble(price + tpDist, digits);
      ok = trade.Buy(lot, _Symbol, ask, sl, tp, "SBgH1-PB");
   }
   else
   {
      sl = NormalizeDouble(price + slDist, digits);
      tp = NormalizeDouble(price - tpDist, digits);
      ok = trade.Sell(lot, _Symbol, bid, sl, tp, "SBgH1-PB");
   }
   PrintFormat("%s %s lot %.2f @ %.*f SL %.*f TP %.*f (tren %d, RSI %.0f<-%.0f) ok=%d",
               _Symbol, wantBuy?"BUY":"SELL", lot, digits, price, digits, sl,
               digits, tp, trend, rsi2, rsi1, ok);
}

//+------------------------------------------------------------------+
//| AUTO-LOT: lot biar rugi saat SL = InpRiskPct% saldo.             |
//+------------------------------------------------------------------+
double CalcLot(double slDist)
{
   if(!InpUseAutoLot) return NormalizeLot(InpLot);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0 || tickVal <= 0)
   {
      Print("tickSize/tickValue tak valid -> pakai lot tetap");
      return NormalizeLot(InpLot);
   }
   double riskMoney  = AccountInfoDouble(ACCOUNT_BALANCE) * (InpRiskPct/100.0);
   double lossPerLot = (slDist / tickSize) * tickVal;
   if(lossPerLot <= 0) return NormalizeLot(InpLot);
   return NormalizeLot(riskMoney / lossPerLot);
}

double NormalizeLot(double lot)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double mn   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step > 0) lot = MathRound(lot/step)*step;
   if(lot < mn) lot = mn;
   if(lot > mx) lot = mx;
   return(lot);
}
//+------------------------------------------------------------------+
