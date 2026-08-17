//+------------------------------------------------------------------+
//|                                          ScalpersBoysBTC.mq5      |
//|   EA KHUSUS BTC — MEAN-REVERSION di H1 (swing).                   |
//|                                                                  |
//|   >>> Edge BTC ada di H1, BUKAN M15 (M15 = koin-flip). <<<        |
//|     * Fade RSI ekstrem di H1: BUY RSI<=30, SELL RSI>=70.          |
//|     * SL = ATR(H1) x 1.2 (swing lebar ~ratusan $), RR 1:2.        |
//|     * TANPA filter tren (di BTC filter malah NURUNIN hasil).      |
//|     * 24/7 (BTC jalan terus, termasuk weekend).                   |
//|     * AUTO-LOT: lot dihitung dari SL biar rugi = InpRiskPct% saldo.|
//|       -> mau BTC $40k/$64k atau akun cent/standard, risiko tetap. |
//|     * Maks 2 posisi barengan.                                     |
//|                                                                  |
//|   Backtest ~2bln (2% risiko/trade): WR 44%, ekspektansi +0.30R,   |
//|   +$25/bln (~25% akun), DD ~$16 (16%), profit tiap bulan.         |
//|   PELENGKAP Gold v6 (ritme beda -> DD gabungan TAK numpuk).       |
//|                                                                  |
//|   Indikator dibaca di bar TERTUTUP (shift 1) -> anti look-ahead.  |
//|   DEMO dulu. Matiin EA BTC M15 lama biar tak dobel.               |
//+------------------------------------------------------------------+
#property copyright "Scalper's Boys"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//--- Input ----------------------------------------------------------
input bool   InpUseAutoLot   = true;    // ON=hitung lot dari risiko% (SANGAT disaranin utk BTC)
input double InpRiskPct      = 2.0;     // risiko per trade (% saldo) kalau auto-lot
input double InpLot          = 0.20;    // lot tetap (dipakai kalau auto-lot OFF)
input double InpRR           = 2.0;     // Reward:Risk (TP = SL x ini)
input double InpAtrMult      = 1.2;     // SL = ATR(H1) x ini
input int    InpAtrPeriod    = 14;      // periode ATR (H1)
input int    InpRsiPeriod    = 14;      // periode RSI (H1)
input double InpRsiLo        = 30.0;    // BUY kalau RSI<=ini (BTC H1: 30, bukan 35)
input double InpRsiHi        = 70.0;    // SELL kalau RSI>=ini (BTC H1: 70)
input int    InpMaxPositions = 2;       // maks posisi barengan
//--- filter tren (DEFAULT OFF: di BTC filter NURUNIN hasil) ----------
input bool   InpRegimeFilter = false;   // ON=skip tren kuat. BTC: biarin OFF.
input int    InpEmaFast      = 21;      // EMA cepat (H1) — hanya kalau filter ON
input int    InpEmaSlow      = 50;      // EMA lambat (H1)
//--- jam & berita (BTC 24/7, default longgar) -----------------------
input bool   InpUseSession   = false;   // BTC: false (24 jam)
input int    InpSessStart    = 0;       // jam GMT mulai (kalau sesi ON)
input int    InpSessEnd      = 24;      // jam GMT selesai
input bool   InpWeekendGuard = false;   // BTC jalan weekend -> false
input bool   InpNewsFilter   = false;   // BTC: default OFF (kalender USD kurang relevan)
input int    InpNewsBeforeMin = 30;     // stop berapa menit SEBELUM berita
input int    InpNewsAfterMin  = 30;     // lanjut berapa menit SETELAH berita
input string InpNewsCurrency = "USD";   // mata uang berita dipantau
input long   InpMagic        = 20260817;// pembeda posisi EA ini (BEDA dari gold v6!)
//--- manajemen SL (default OFF) -------------------------------------
input bool   InpBreakeven    = false;   // geser SL ke entry saat profit cukup
input double InpBEatR        = 1.0;     // trigger breakeven (dalam R)
input double InpBElockR      = 0.0;     // kunci berapa R saat BE
input bool   InpTrail        = false;   // trailing setelah profit lanjut
input double InpTrailStart   = 1.5;     // mulai trailing di berapa R
input double InpTrailGap     = 1.0;     // SL ketinggalan berapa R

//--- Handle indikator (SEMUA di H1) ---------------------------------
int      hEmaF, hEmaS, hRsi, hAtr;
datetime lastBar = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   hRsi  = iRSI(_Symbol, PERIOD_H1, InpRsiPeriod, PRICE_CLOSE);
   hAtr  = iATR(_Symbol, PERIOD_H1, InpAtrPeriod);
   hEmaF = iMA(_Symbol, PERIOD_H1, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   hEmaS = iMA(_Symbol, PERIOD_H1, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
   if(hRsi==INVALID_HANDLE || hAtr==INVALID_HANDLE ||
      hEmaF==INVALID_HANDLE || hEmaS==INVALID_HANDLE)
   {
      Print("Gagal membuat handle indikator");
      return(INIT_FAILED);
   }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(50);   // BTC gerak cepat -> deviasi lebih longgar
   PrintFormat("ScalpersBoysBTC aktif di %s — %s RR 1:%.1f RSI %.0f/%.0f (H1)",
               _Symbol, InpUseAutoLot?"auto-lot "+DoubleToString(InpRiskPct,1)+"%":"lot "+DoubleToString(InpLot,2),
               InpRR, InpRsiLo, InpRsiHi);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hRsi);  IndicatorRelease(hAtr);
   IndicatorRelease(hEmaF); IndicatorRelease(hEmaS);
}

//--- ambil 1 nilai buffer di posisi shift ---------------------------
double Val(int handle, int shift)
{
   double b[];
   if(CopyBuffer(handle, 0, shift, 1, b) <= 0) return(EMPTY_VALUE);
   return(b[0]);
}

//--- bar H1 baru? ---------------------------------------------------
bool NewH1Bar()
{
   datetime t = iTime(_Symbol, PERIOD_H1, 0);
   if(t == lastBar) return(false);
   lastBar = t;
   return(true);
}

//--- jumlah posisi terbuka milik EA ini -----------------------------
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

//--- boleh entry sekarang? (sesi + weekend) -------------------------
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

//--- Ada berita high-impact? (default OFF utk BTC) ------------------
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
//| Kelola SL posisi terbuka: breakeven + trailing (default OFF)     |
//+------------------------------------------------------------------+
void ManageOpen()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   int    dg  = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol ||
         PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;

      long   type  = PositionGetInteger(POSITION_TYPE);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl    = PositionGetDouble(POSITION_SL);
      double tp    = PositionGetDouble(POSITION_TP);
      if(tp == 0) continue;

      double oneR = MathAbs(tp-entry)/InpRR;
      if(oneR <= 0) continue;

      double profitR, sign;
      if(type==POSITION_TYPE_BUY) { profitR=(bid-entry)/oneR; sign=1.0;  }
      else                        { profitR=(entry-ask)/oneR; sign=-1.0; }

      double lockR = -9999.0;
      if(InpTrail && profitR >= InpTrailStart)
         lockR = profitR - InpTrailGap;
      else if(InpBreakeven && profitR >= InpBEatR)
         lockR = InpBElockR;
      if(lockR <= -9999.0) continue;

      double newSL = NormalizeDouble(entry + sign*lockR*oneR, dg);
      bool better = (type==POSITION_TYPE_BUY) ? (sl==0 || newSL>sl)
                                              : (sl==0 || newSL<sl);
      if(better) trade.PositionModify(tk, newSL, tp);
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   ManageOpen();                            // kelola SL tiap tick (BE+trailing)
   if(!NewH1Bar()) return;                  // entry: evaluasi 1x per bar H1
   if(!InSession()) return;
   if(IsNewsBlackout()) return;
   if(CountMyPositions() >= InpMaxPositions) return;

   // indikator di bar TERTUTUP (shift 1)
   double rsi  = Val(hRsi,1),  atr  = Val(hAtr,1);
   if(rsi==EMPTY_VALUE || atr==EMPTY_VALUE || atr<=0) return;

   // filter tren opsional (DEFAULT OFF di BTC)
   if(InpRegimeFilter)
   {
      double emaF = Val(hEmaF,1), emaS = Val(hEmaS,1);
      if(emaF==EMPTY_VALUE || emaS==EMPTY_VALUE) return;
      if(MathAbs(emaF-emaS) > 1.5*atr) return;   // skip tren kuat
   }

   // MEAN-REVERSION: fade ekstrem RSI 30/70
   bool wantBuy  = (rsi <= InpRsiLo);
   bool wantSell = (rsi >= InpRsiHi);
   if(!wantBuy && !wantSell) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double price = wantBuy ? ask : bid;

   // SL = ATR(H1) x mult (polos, sesuai sim). Hormati jarak minimum broker.
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
      ok = trade.Buy(lot, _Symbol, ask, sl, tp, "SBbtc-MR");
   }
   else
   {
      sl = NormalizeDouble(price + slDist, digits);
      tp = NormalizeDouble(price - tpDist, digits);
      ok = trade.Sell(lot, _Symbol, bid, sl, tp, "SBbtc-MR");
   }
   PrintFormat("%s %s lot %.2f @ %.*f SL %.*f TP %.*f (RSI %.0f, ATR %.1f) ok=%d",
               _Symbol, wantBuy?"BUY":"SELL", lot, digits, price, digits, sl,
               digits, tp, rsi, atr, ok);
}

//+------------------------------------------------------------------+
//| AUTO-LOT: hitung lot biar rugi saat SL kena = InpRiskPct% saldo. |
//|   Robust ke contract size (cent/standard) & harga BTC berapapun. |
//|   rugi_per_lot = (slDist / tickSize) * tickValue                 |
//|   lot = (saldo * risk%) / rugi_per_lot                           |
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
   double lossPerLot = (slDist / tickSize) * tickVal;   // rugi 1.0 lot kalau SL kena
   if(lossPerLot <= 0) return NormalizeLot(InpLot);
   double lot = riskMoney / lossPerLot;
   return NormalizeLot(lot);
}

//--- bulatkan lot ke volume step + batas broker ---------------------
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
