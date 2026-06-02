//+------------------------------------------------------------------+
//|                                                      TrendEA.mq5  |
//|   EA tren-following: tren EMA50/200 (TF tren) + entry RSI pullback |
//|   (TF entry, default M30) + ATR stop. Timeframe bisa diatur lewat  |
//|   input InpTrendTF / InpEntryTF.                                   |
//|   RR 1:3. LAYERING: 1 entry awal + maks 2 layer (total 3 posisi)  |
//|   searah, ditambah hanya saat harga bergerak menguntungkan        |
//|   (pyramiding). Operasi 24 jam; batas trade/hari via InpMaxTradesDay|
//|   News blackout + bias sentimen (berita + COT) lewat data_service |
//|   (HTTP /context).                                                |
//|                                                                   |
//|   Lot rule: <400 USD = 0.01 ; >=400 = 0.02 ; tiap +200 USD naik   |
//|   0.01 ; maksimal 0.05. (lot ini berlaku per layer)              |
//|                                                                   |
//|   PENTING: aktifkan WebRequest untuk URL data_service di          |
//|   Tools > Options > Expert Advisors > Allow WebRequest for ...    |
//+------------------------------------------------------------------+
#property copyright "forex-trading-bot"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- Input strategi ----------------------------------------------------
input double          InpRewardRatio  = 3.0;          // Risk : Reward (1 : X)  -> 1:3
input int             InpMaxTradesDay = 6;            // Maks trade per hari (0 = tanpa batas)
input ENUM_TIMEFRAMES InpTrendTF      = PERIOD_H4;    // Timeframe arah tren (EMA)
input ENUM_TIMEFRAMES InpEntryTF      = PERIOD_M30;   // Timeframe entry (RSI/ATR & evaluasi)
input int    InpEmaFast       = 50;      // EMA cepat (tren)
input int    InpEmaSlow       = 200;     // EMA lambat (tren)
input int    InpRsiPeriod     = 14;      // Periode RSI (entry)
input double InpRsiBuyMin     = 40.0;    // RSI min saat cari BUY (pullback bawah)
input double InpRsiBuyMax     = 60.0;    // RSI maks saat cari BUY (pullback atas)
input double InpRsiSellMin    = 40.0;    // RSI min saat cari SELL (pullback bawah)
input double InpRsiSellMax    = 60.0;    // RSI maks saat cari SELL (pullback atas)
input int    InpAtrPeriod     = 14;      // Periode ATR (stop)
input double InpAtrSlMult     = 1.5;     // SL = ATR * mult
input long   InpMagic         = 880088;  // Magic number

//--- Input lot bertingkat (sesuai ekuitas) ----------------------------
input double InpLotBelow      = 0.01;    // Lot saat ekuitas < InpEquityBase
input double InpLotBase       = 0.02;    // Lot saat ekuitas >= InpEquityBase
input double InpEquityBase    = 400.0;   // Batas ekuitas (USD) mulai lot dasar
input double InpEquityStep    = 200.0;   // Tiap kenaikan ekuitas (USD) ...
input double InpLotStep       = 0.01;    // ... lot ditambah sebanyak ini
input double InpLotMax        = 0.05;    // Lot maksimal (per layer)

//--- Input layering (1 entry + 2 layer) -------------------------------
input bool   InpUseLayering   = true;    // Aktifkan penambahan layer (pyramiding)
input int    InpMaxLayers     = 3;       // Total posisi searah maksimal (1 entry + 2 layer)
input double InpLayerStepAtr  = 1.0;     // Jarak antar layer = ATR * ini (harus profit dulu)
input bool   InpBreakevenLayer= true;    // Saat tambah layer, geser SL posisi lama ke BE
input double InpMaxSpreadAtr  = 0.25;    // Skip entry bila spread > ATR * ini (jaga timing)

//--- Input integrasi data_service -------------------------------------
input string InpDataServiceUrl = "https://your-app.up.railway.app"; // URL Railway
input string InpApiKey         = "";     // X-Api-Key (kosong = tanpa auth)
input bool   InpUseNewsFilter  = true;   // Hindari entry saat blackout berita
input bool   InpUseSentiment   = true;   // Hanya entry searah bias sentimen (COT+berita)

//--- Handle indikator --------------------------------------------------
int    hEmaFastH4, hEmaSlowH4;
int    hRsiH1, hAtrH1;
CTrade trade;

// Penghitung trade harian
int    g_tradesToday = 0;
datetime g_dayStart  = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);   // IOC/FOK sesuai broker (penting utk XAUUSD)
   trade.SetDeviationInPoints(30);          // toleransi slippage agar tak gampang requote

   hEmaFastH4 = iMA(_Symbol, InpTrendTF, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   hEmaSlowH4 = iMA(_Symbol, InpTrendTF, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
   hRsiH1     = iRSI(_Symbol, InpEntryTF, InpRsiPeriod, PRICE_CLOSE);
   hAtrH1     = iATR(_Symbol, InpEntryTF, InpAtrPeriod);

   if(hEmaFastH4==INVALID_HANDLE || hEmaSlowH4==INVALID_HANDLE ||
      hRsiH1==INVALID_HANDLE || hAtrH1==INVALID_HANDLE)
   {
      Print("Gagal inisialisasi indikator");
      return(INIT_FAILED);
   }
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(hEmaFastH4);
   IndicatorRelease(hEmaSlowH4);
   IndicatorRelease(hRsiH1);
   IndicatorRelease(hAtrH1);
}

//+------------------------------------------------------------------+
//| Reset counter saat ganti hari                                    |
//+------------------------------------------------------------------+
void RefreshDailyCounter()
{
   MqlDateTime t; TimeToStruct(TimeCurrent(), t);
   t.hour=0; t.min=0; t.sec=0;
   datetime today = StructToTime(t);
   if(today != g_dayStart)
   {
      g_dayStart    = today;
      g_tradesToday = CountTodayDeals();
   }
}

int CountTodayDeals()
{
   int n=0;
   HistorySelect(g_dayStart, TimeCurrent());
   for(int i=HistoryDealsTotal()-1; i>=0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(ticket, DEAL_MAGIC)==InpMagic &&
         HistoryDealGetInteger(ticket, DEAL_ENTRY)==DEAL_ENTRY_IN)
         n++;
   }
   return n;
}

//+------------------------------------------------------------------+
//| Ambil context dari data_service: trade_allowed + sentiment_bias  |
//| Return true bila berhasil. allowed & bias diisi via referensi.   |
//+------------------------------------------------------------------+
bool FetchContext(bool &allowed, string &bias)
{
   allowed = true; bias = "flat";

   // WebRequest tidak tersedia di Strategy Tester. Supaya EA tetap bisa
   // di-backtest, anggap context "boleh entry" tanpa bias arah.
   if(MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION))
   {
      allowed = true; bias = "flat";
      return true;
   }

   if(InpDataServiceUrl=="" ) return false;

   string url = InpDataServiceUrl + "/context?symbol=" + _Symbol;
   string headers = "";
   if(InpApiKey!="") headers = "X-Api-Key: " + InpApiKey + "\r\n";

   char   post[]; char result[]; string resultHeaders;
   int timeout = 5000;
   ResetLastError();
   int code = WebRequest("GET", url, headers, timeout, post, result, resultHeaders);
   if(code != 200)
   {
      PrintFormat("WebRequest gagal (code=%d, err=%d). Cek whitelist URL.", code, GetLastError());
      return false; // gagal -> EA bisa pilih fail-safe (lihat OnTick)
   }

   string body = CharArrayToString(result);
   allowed = (StringFind(body, "\"trade_allowed\":true") >= 0);
   if(StringFind(body, "\"sentiment_bias\":\"long\"")  >= 0) bias="long";
   else if(StringFind(body, "\"sentiment_bias\":\"short\"") >= 0) bias="short";
   return true;
}

//+------------------------------------------------------------------+
//| Baca nilai indikator buffer index ke-shift                       |
//+------------------------------------------------------------------+
double Val(int handle, int shift)
{
   double buf[];
   if(CopyBuffer(handle, 0, shift, 1, buf) <= 0) return 0.0;
   return buf[0];
}

//+------------------------------------------------------------------+
//| Lot bertingkat berdasarkan ekuitas akun.                         |
//|   ekuitas < InpEquityBase (400)         -> InpLotBelow (0.01)     |
//|   ekuitas >= InpEquityBase (400)        -> InpLotBase  (0.02)     |
//|   tiap kelipatan InpEquityStep (200) di atas base -> +InpLotStep  |
//|   dibatasi InpLotMax (0.05) & batas volume broker.               |
//+------------------------------------------------------------------+
double LotForEquity()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double lots;

   if(equity < InpEquityBase)
   {
      lots = InpLotBelow;
   }
   else
   {
      // Berapa kali ekuitas melewati kelipatan InpEquityStep di atas base.
      int steps = (int)MathFloor((equity - InpEquityBase) / InpEquityStep);
      lots = InpLotBase + steps * InpLotStep;
   }

   if(lots > InpLotMax) lots = InpLotMax;   // cap 0.05

   // Normalisasi ke step & batas volume broker.
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step > 0) lots = MathRound(lots/step)*step;
   if(lots < minL) lots = minL;
   if(lots > maxL) lots = maxL;
   return lots;
}

//+------------------------------------------------------------------+
//| Info posisi EA pada simbol ini.                                  |
//|   return  : jumlah posisi searah (semua posisi kita searah).     |
//|   dir     : +1 buy, -1 sell, 0 tak ada.                          |
//|   extreme : entry paling "jauh" searah tren (tertinggi utk buy,  |
//|             terendah utk sell) -> dipakai ukur jarak layer.      |
//+------------------------------------------------------------------+
int CountMyPositions(int &dir, double &extreme)
{
   int count=0; dir=0; extreme=0.0; bool first=true;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;

      long   type = PositionGetInteger(POSITION_TYPE);
      double op   = PositionGetDouble(POSITION_PRICE_OPEN);
      int    d    = (type==POSITION_TYPE_BUY) ? 1 : -1;
      dir = d;     // semua posisi kita selalu searah (tidak hedging)
      count++;
      if(first) { extreme=op; first=false; }
      else if(d==1) extreme = MathMax(extreme, op);
      else          extreme = MathMin(extreme, op);
   }
   return count;
}

//+------------------------------------------------------------------+
//| Geser SL semua posisi searah ke breakeven (harga entry masing-   |
//| masing) bila posisi sudah profit. Dipanggil saat menambah layer. |
//+------------------------------------------------------------------+
void MovePositionsToBreakeven(int dir)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;

      double op  = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl  = PositionGetDouble(POSITION_SL);
      double tp  = PositionGetDouble(POSITION_TP);
      double be  = NormalizeDouble(op, _Digits);

      // Hanya geser bila harga sudah melewati entry (BE valid & menguntungkan).
      if(dir==1 && bid > op && (sl < be || sl==0.0))
         trade.PositionModify(ticket, be, tp);
      else if(dir==-1 && ask < op && (sl > be || sl==0.0))
         trade.PositionModify(ticket, be, tp);
   }
}

//+------------------------------------------------------------------+
//| Jalankan logika hanya sekali per bar baru (timeframe entry)      |
//+------------------------------------------------------------------+
bool IsNewEntryBar()
{
   static datetime last = 0;
   datetime cur = iTime(_Symbol, InpEntryTF, 0);
   if(cur != last) { last = cur; return true; }
   return false;
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewEntryBar()) return;    // evaluasi sekali per bar (timeframe entry)
   RefreshDailyCounter();

   // Batas trade/hari opsional (0 = tanpa batas, operasi 24 jam penuh).
   if(InpMaxTradesDay > 0 && g_tradesToday >= InpMaxTradesDay) return;

   //--- 1) Tren dari H4 (EMA 50 vs 200) -------------------------------
   double emaFast = Val(hEmaFastH4, 1);
   double emaSlow = Val(hEmaSlowH4, 1);
   if(emaFast==0 || emaSlow==0) return;
   int trend = 0;                  // +1 uptrend, -1 downtrend
   if(emaFast > emaSlow) trend = 1;
   else if(emaFast < emaSlow) trend = -1;
   if(trend==0) return;

   //--- 2) Konfirmasi entry H1 (RSI pullback, pakai band) -------------
   double rsi = Val(hRsiH1, 1);
   bool wantBuy  = (trend== 1 && rsi >= InpRsiBuyMin  && rsi <= InpRsiBuyMax);
   bool wantSell = (trend==-1 && rsi >= InpRsiSellMin && rsi <= InpRsiSellMax);
   if(!wantBuy && !wantSell) return;
   int sigDir = wantBuy ? 1 : -1;

   //--- 3) ATR + jaga timing (spread tidak boleh terlalu lebar) -------
   double atr = Val(hAtrH1, 1);
   if(atr<=0) return;

   double ask  = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid  = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double spread = ask - bid;
   if(InpMaxSpreadAtr > 0 && spread > InpMaxSpreadAtr * atr)
   {
      Print("Spread terlalu lebar (timing buruk) -> skip"); // mis. saat rollover/news
      return;
   }

   //--- 4) Cek posisi terbuka & tentukan: entry baru atau tambah layer
   int    dir=0; double extreme=0.0;
   int    nPos = CountMyPositions(dir, extreme);

   if(nPos > 0)
   {
      if(!InpUseLayering) return;                 // layering mati -> 1 posisi saja
      if(sigDir != dir) return;                   // sinyal lawan arah -> jangan hedging
      if(nPos >= InpMaxLayers) return;            // sudah 1 entry + 2 layer
      // Layer baru hanya kalau harga sudah bergerak menguntungkan >= step ATR
      // dari entry layer terjauh (pyramiding ke arah profit, bukan averaging).
      double price = (dir==1) ? ask : bid;
      double moved = (dir==1) ? (price - extreme) : (extreme - price);
      if(moved < InpLayerStepAtr * atr) return;
   }

   //--- 5) Filter berita & bias sentimen dari data_service ------------
   bool allowed=true; string bias="flat";
   bool ctxOk = FetchContext(allowed, bias);
   if(InpUseNewsFilter)
   {
      // Fail-safe: kalau data_service tak terjangkau, JANGAN entry.
      if(!ctxOk) { Print("Context gagal -> skip entry (fail-safe)"); return; }
      if(!allowed) { Print("Blackout berita -> skip entry"); return; }
   }
   if(InpUseSentiment && ctxOk && bias!="flat")
   {
      if(wantBuy  && bias!="long")  { Print("BUY ditolak: bias sentimen bukan long"); return; }
      if(wantSell && bias!="short") { Print("SELL ditolak: bias sentimen bukan short"); return; }
   }

   //--- 6) Hitung SL/TP berbasis ATR ----------------------------------
   double slDist = atr * InpAtrSlMult;
   double tpDist = slDist * InpRewardRatio;

   // Pastikan jarak SL memenuhi batas minimum broker (stops level).
   double point     = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   long   stopLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minStop   = stopLevel * point;
   if(minStop > 0 && slDist < minStop)
   {
      slDist = minStop;
      tpDist = slDist * InpRewardRatio;
   }

   double lots = LotForEquity();
   if(lots<=0) return;

   // Sebelum tambah layer, amankan posisi lama ke breakeven.
   if(nPos > 0 && InpBreakevenLayer) MovePositionsToBreakeven(dir);

   int layerNo = nPos + 1;
   string note = StringFormat("TrendEA L%d", layerNo);

   bool ok=false;
   if(wantBuy)
   {
      double sl = NormalizeDouble(ask - slDist, _Digits);
      double tp = NormalizeDouble(ask + tpDist, _Digits);
      ok = trade.Buy(lots, _Symbol, 0.0, sl, tp, note);
   }
   else if(wantSell)
   {
      double sl = NormalizeDouble(bid + slDist, _Digits);
      double tp = NormalizeDouble(bid - tpDist, _Digits);
      ok = trade.Sell(lots, _Symbol, 0.0, sl, tp, note);
   }

   if(ok)
   {
      g_tradesToday++;
      PrintFormat("Order terkirim (layer %d/%d). trade ke-%d hari ini. bias=%s",
                  layerNo, InpMaxLayers, g_tradesToday, bias);
   }
   else
   {
      PrintFormat("Order gagal: %d - %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
   }
}
//+------------------------------------------------------------------+
