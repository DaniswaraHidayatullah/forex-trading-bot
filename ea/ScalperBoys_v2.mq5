//+------------------------------------------------------------------+
//|                                           ScalperBoys_v2.mq5      |
//|   EA v2 STANDALONE (tanpa server) — mirror strategi Python v2.    |
//|                                                                  |
//|   >>> VERSI 2.3 (config aktif) = MEAN-REVERSION + filter penuh <<<|
//|     * Entry : fade ekstrem M15 RSI(14) -> BUY<=35, SELL>=65       |
//|     * Filter: tren ON (skip tren kuat) + news ON + sesi 05-21 GMT |
//|     * SL    : ATR(14 M15) x 1.2, DILEBARKAN anti-spike            |
//|               (di luar wick 12 bar + 0.5 ATR + bantalan spread)   |
//|     * TP    : SL x RR (1:2) ; TANPA batas harian (maks 2 posisi)  |
//|                                                                  |
//|   Changelog:                                                     |
//|     2.0  = trend-pullback (H1 EMA21/50) -> BACKTEST RUGI di regime|
//|            choppy. Ditinggalin. (set InpEntryMode=0 utk balik.)   |
//|     2.2  = mean-reversion RSI30/70 + filter tren/news, cap 6->10. |
//|     2.3  = RSI 35/65 (2.9 sinyal/hr, WR49%, +$98/bln), cap harian |
//|            DIHAPUS (tanpa batas). INI YANG DIPAKAI.               |
//|                                                                  |
//|   Indikator dibaca di bar TERTUTUP (shift 1) -> anti look-ahead.  |
//|   Akun DEMO dulu. Default XAUUSD; BTCUSD set UseSession/Weekend=false|
//+------------------------------------------------------------------+
#property copyright "Scalper's Boys"
#property version   "2.30"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//--- Input (bisa diubah di dialog EA / Strategy Tester) -------------
input double InpLot          = 0.20;    // Lot tetap
input double InpRR           = 2.0;     // Reward:Risk (TP = SL x ini)
input double InpAtrMult      = 1.2;     // SL = ATR x ini (sebelum anti-spike)
input int    InpAtrPeriod    = 14;      // periode ATR (M15)
input int    InpRsiPeriod    = 14;      // periode RSI (M15)
input int    InpEntryMode    = 1;       // 0=trend-pullback, 1=MEAN-REVERSION (dipakai)
// Config terpilih: RSI 30/70, maks 2 posisi, RR 1:2, filter tren ON + filter
// berita ON. Backtest (sesi ON, filter tren ON): ~1.6 sinyal/hari, WR 49%,
// ekspektansi +0.455R, drawdown ~16%. Filter tren nyegah fading tren kuat
// (biang loss beruntun); BE/trail OFF (terbukti nurunin hasil).
input bool   InpRegimeFilter = true;    // (mean-rev) ON=skip tren kuat (hindari fading tren)
input double InpRsiLo        = 35.0;    // mean-rev: BUY kalau RSI<=ini (35: ~2.9 sinyal/hr, WR49%, +$98/bln backtest)
input double InpRsiHi        = 65.0;    // mean-rev: SELL kalau RSI>=ini
input int    InpEmaFast      = 21;      // EMA cepat (H1)
input int    InpEmaSlow      = 50;      // EMA lambat (H1)
input double InpSpreadPad    = 0.30;    // bantalan anti-spike (dalam harga)
input bool   InpUseSession   = true;    // batasi jam (emas). BTC: false
input int    InpSessStart    = 5;       // jam GMT mulai entry
input int    InpSessEnd      = 21;      // jam GMT selesai entry
input bool   InpWeekendGuard = true;    // skip weekend (emas). BTC: false
input int    InpMaxPositions = 2;       // maks posisi TERBUKA barengan (magic ini)
input int    InpMaxTradesDay = 0;       // maks TOTAL entry per hari (0=TANPA batas).
// Backtest 35/65: tanpa batas +$98/bln vs cap-10 +$89 (DD sama ~$18, max 13/hari).
// Resiko real-time tetap dijaga InpMaxPositions=2 (maks 2 posisi barengan).
input long   InpMagic        = 20260801;// pembeda posisi EA ini
//--- Filter BERITA (stop entry sekitar news high-impact) ------------
// Pakai Economic Calendar bawaan MT5 (butuh calendar terisi; hanya LIVE/DEMO,
// TIDAK jalan di Strategy Tester). Stop buka posisi baru di jendela berita.
input bool   InpNewsFilter   = true;    // stop entry sekitar berita high-impact
input int    InpNewsBeforeMin = 30;     // stop mulai berapa menit SEBELUM berita
input int    InpNewsAfterMin  = 30;     // lanjut lagi berapa menit SETELAH berita
input string InpNewsCurrency = "USD";   // mata uang berita yg dipantau (emas=USD)
//--- Manajemen SL: BREAKEVEN + TRAILING (kunci profit) --------------
// DEFAULT OFF: backtest 60hr XAU menunjukkan BE+trail malah menurunkan hasil
// (-2.5R polos -> -4.5R) krn trailing mancung winner yg harusnya lari ke 2R.
// Nyalain utk eksperimen (mungkin cocok utk aset yg sering near-miss spt BTC).
input bool   InpBreakeven    = false;   // geser SL ke entry saat profit cukup
input double InpBEatR        = 1.0;     // trigger breakeven (dalam R)
input double InpBElockR       = 0.0;    // kunci berapa R saat BE (0 = pas entry)
input bool   InpTrail        = false;   // trailing setelah profit lanjut
input double InpTrailStart    = 1.5;    // mulai trailing di berapa R
input double InpTrailGap      = 1.0;    // SL ketinggalan berapa R di belakang harga

//--- Handle indikator ----------------------------------------------
int      hEmaF, hEmaS, hRsi, hAtr;
datetime lastBar = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   hEmaF = iMA(_Symbol, PERIOD_H1, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   hEmaS = iMA(_Symbol, PERIOD_H1, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
   hRsi  = iRSI(_Symbol, PERIOD_M15, InpRsiPeriod, PRICE_CLOSE);
   hAtr  = iATR(_Symbol, PERIOD_M15, InpAtrPeriod);
   if(hEmaF==INVALID_HANDLE || hEmaS==INVALID_HANDLE ||
      hRsi==INVALID_HANDLE  || hAtr==INVALID_HANDLE)
   {
      Print("Gagal membuat handle indikator");
      return(INIT_FAILED);
   }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(30);
   Print("ScalperBoys_v2 aktif di ", _Symbol, " — lot ", InpLot, " RR 1:", InpRR);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hEmaF); IndicatorRelease(hEmaS);
   IndicatorRelease(hRsi);  IndicatorRelease(hAtr);
}

//--- ambil 1 nilai buffer di posisi shift ---------------------------
double Val(int handle, int shift)
{
   double b[];
   if(CopyBuffer(handle, 0, shift, 1, b) <= 0) return(EMPTY_VALUE);
   return(b[0]);
}

//--- bar M15 baru? --------------------------------------------------
bool NewM15Bar()
{
   datetime t = iTime(_Symbol, PERIOD_M15, 0);
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

//--- berapa TOTAL entry (buka posisi) hari ini utk magic ini? -------
int TradesOpenedToday()
{
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime dayStart = StructToTime(dt);
   int count = 0;
   if(HistorySelect(dayStart, TimeCurrent()))
   {
      for(int i = HistoryDealsTotal()-1; i >= 0; i--)
      {
         ulong tk = HistoryDealGetTicket(i);
         if(tk == 0) continue;
         if(HistoryDealGetString(tk, DEAL_SYMBOL) == _Symbol &&
            HistoryDealGetInteger(tk, DEAL_MAGIC) == InpMagic &&
            HistoryDealGetInteger(tk, DEAL_ENTRY) == DEAL_ENTRY_IN) count++;
      }
   }
   return(count);
}

//--- boleh entry sekarang? (sesi + weekend) -------------------------
bool InSession()
{
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   if(InpWeekendGuard)
   {
      if(dt.day_of_week == 6) return(false);                    // Sabtu
      if(dt.day_of_week == 5 && dt.hour >= 21) return(false);   // Jumat malam
      if(dt.day_of_week == 0 && dt.hour < 22) return(false);    // Minggu pagi
   }
   if(InpUseSession)
      if(dt.hour < InpSessStart || dt.hour >= InpSessEnd) return(false);
   return(true);
}

//--- Ada berita high-impact di jendela sekarang? (Economic Calendar) -
//    Return true = lagi blackout berita -> jangan buka posisi baru.
//    Hanya jalan LIVE/DEMO (calendar tak tersedia di Strategy Tester).
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
      if(ev.importance == CALENDAR_IMPORTANCE_HIGH)
         return(true);   // ada berita high-impact di window -> stop entry
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Kelola SL posisi terbuka: breakeven + trailing (dipanggil /tick) |
//|   1R diturunkan dari jarak TP (tak berubah) / RR.                |
//|   profit >= InpBEatR         -> SL ke entry (+InpBElockR).        |
//|   profit >= InpTrailStart    -> SL seret, ketinggalan InpTrailGap.|
//|   SL hanya digeser MAJU (tak pernah dilonggarkan).               |
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

      double oneR = MathAbs(tp-entry)/InpRR;      // 1R (dari TP, tak berubah)
      if(oneR <= 0) continue;

      double profitR, sign;
      if(type==POSITION_TYPE_BUY) { profitR=(bid-entry)/oneR; sign=1.0;  }
      else                        { profitR=(entry-ask)/oneR; sign=-1.0; }

      double lockR = -9999.0;                     // -9999 = belum aktif
      if(InpTrail && profitR >= InpTrailStart)
         lockR = profitR - InpTrailGap;           // seret di belakang harga
      else if(InpBreakeven && profitR >= InpBEatR)
         lockR = InpBElockR;                       // breakeven
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
   if(!NewM15Bar()) return;                 // entry: evaluasi 1x per bar M15
   if(!InSession()) return;
   if(IsNewsBlackout()) return;             // stop entry sekitar berita high-impact
   if(InpMaxTradesDay > 0 && TradesOpenedToday() >= InpMaxTradesDay) return; // batas harian
   if(CountMyPositions() >= InpMaxPositions) return;

   // indikator di bar TERTUTUP (shift 1)
   double emaF = Val(hEmaF,1), emaS = Val(hEmaS,1);
   double rsi  = Val(hRsi,1),  atr  = Val(hAtr,1);
   if(emaF==EMPTY_VALUE || emaS==EMPTY_VALUE ||
      rsi==EMPTY_VALUE  || atr==EMPTY_VALUE  || atr<=0) return;

   bool wantBuy=false, wantSell=false;
   if(InpEntryMode==1)   // MEAN-REVERSION: fade ekstrem (RSI<=lo beli, RSI>=hi jual)
   {
      // filter regime: kalau tren KUAT (EMA jauh), jangan fade -> skip
      if(InpRegimeFilter && MathAbs(emaF-emaS) > 1.5*atr) return;
      wantBuy  = (rsi <= InpRsiLo);
      wantSell = (rsi >= InpRsiHi);
   }
   else                  // TREND-PULLBACK (lama): searah tren + RSI di band
   {
      int trend = (emaF>emaS) ? 1 : ((emaF<emaS) ? -1 : 0);
      if(trend == 0) return;
      wantBuy  = (trend==1  && rsi>=InpRsiLo && rsi<=InpRsiHi);
      wantSell = (trend==-1 && rsi>=InpRsiLo && rsi<=InpRsiHi);
   }
   if(!wantBuy && !wantSell) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double price = wantBuy ? ask : bid;

   // ANTI-SPIKE: SL di luar wick 12 bar M15 + 0.5 ATR + bantalan spread
   double highs[], lows[];
   if(CopyHigh(_Symbol,PERIOD_M15,1,12,highs) < 12) return;
   if(CopyLow (_Symbol,PERIOD_M15,1,12,lows ) < 12) return;
   double recHi = highs[ArrayMaximum(highs)];
   double recLo = lows [ArrayMinimum(lows )];
   double slDist = atr * InpAtrMult;
   double wick = wantBuy ? (price-recLo) + 0.5*atr + InpSpreadPad
                         : (recHi-price) + 0.5*atr + InpSpreadPad;
   if(wick > slDist) slDist = wick;

   // hormati jarak minimum broker (stops level)
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minDist = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   if(slDist < minDist*1.2) slDist = minDist*1.2;
   double tpDist = slDist * InpRR;

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double lot    = NormalizeLot(InpLot);
   double sl, tp;
   bool ok;
   if(wantBuy)
   {
      sl = NormalizeDouble(price - slDist, digits);
      tp = NormalizeDouble(price + tpDist, digits);
      ok = trade.Buy(lot, _Symbol, ask, sl, tp, "SB v2");
   }
   else
   {
      sl = NormalizeDouble(price + slDist, digits);
      tp = NormalizeDouble(price - tpDist, digits);
      ok = trade.Sell(lot, _Symbol, bid, sl, tp, "SB v2");
   }
   PrintFormat("%s %s @ %.*f SL %.*f TP %.*f (RSI %.0f, ATR %.2f) ok=%d",
               _Symbol, wantBuy?"BUY":"SELL", digits, price, digits, sl,
               digits, tp, rsi, atr, ok);
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
