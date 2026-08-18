//+------------------------------------------------------------------+
//| XAU_M15_Dual_Regime_4Pos.mq5                                   |
//| XAUUSDc M15: Momentum Trend + Bollinger Range Reversion         |
//| Designed for an MT5 hedging account; calculations use M15.      |
//+------------------------------------------------------------------+
#property copyright "Research build"
#property version   "2.00"
#property strict

enum ENUM_DIRECTION_MODE
  {
   DIRECTION_BOTH       = 0,
   DIRECTION_LONG_ONLY  = 1,
   DIRECTION_SHORT_ONLY = 2
  };

enum ENUM_STRATEGY_ID
  {
   STRATEGY_TREND = 1,
   STRATEGY_RANGE = 2
  };

input group "General"
input bool                EnableTrend            = true;
input bool                EnableRange            = true;
input ENUM_DIRECTION_MODE DirectionMode          = DIRECTION_BOTH;

input group "Trend strategy (M15, 24 hours)"
input int                 TrendMomentumBars       = 8;
input double              TrendMomentumATR        = 1.60;
input int                 TrendEMAPeriod           = 50;
input int                 TrendATRPeriod           = 14;
input double              TrendStopATR             = 2.00;
input double              TrendRewardRisk          = 2.00;
input int                 TrendMaxHoldingBars      = 48;

input group "Range strategy (M15, 24 hours)"
input int                 RangeWindow              = 16;
input double              RangeZEntry              = 1.50;
input int                 RangeRSIPeriod           = 14;
input double              RangeRSILongMaximum      = 35.0;
input double              RangeRSIShortMinimum     = 65.0;
input int                 RangeFastEMA             = 50;
input int                 RangeSlowEMA             = 200;
input double              RangeMaxTrendGapATR      = 3.00;
input double              RangeStopATR             = 2.00;
input double              RangeRewardRisk          = 1.20;
input int                 RangeMaxHoldingBars      = 24;

input group "Risk and execution"
input double              RiskPercentPerPosition  = 2.00;
input int                 MaxPositionsPerStrategy = 2;
input int                 MaxTotalPositions       = 4;
input double              MaxSpreadPrice          = 0.60;
input ulong               TrendMagicNumber        = 260818151;
input ulong               RangeMagicNumber        = 260818152;
input int                 DeviationPoints         = 50;

int      atr_handle       = INVALID_HANDLE;
int      trend_ema_handle = INVALID_HANDLE;
int      fast_ema_handle  = INVALID_HANDLE;
int      slow_ema_handle  = INVALID_HANDLE;
int      rsi_handle       = INVALID_HANDLE;
datetime last_bar_time    = 0;

//+------------------------------------------------------------------+
ulong StrategyMagic(const ENUM_STRATEGY_ID strategy)
  {
   return(strategy == STRATEGY_TREND ? TrendMagicNumber : RangeMagicNumber);
  }

//+------------------------------------------------------------------+
string StrategyLabel(const ENUM_STRATEGY_ID strategy)
  {
   return(strategy == STRATEGY_TREND ? "Trend" : "Range");
  }

//+------------------------------------------------------------------+
int StrategyMaxHoldingBars(const ENUM_STRATEGY_ID strategy)
  {
   return(strategy == STRATEGY_TREND ? TrendMaxHoldingBars : RangeMaxHoldingBars);
  }

//+------------------------------------------------------------------+
string BatchLockKey(const ENUM_STRATEGY_ID strategy)
  {
   return("XAU_M15_DUAL_BATCH_" +
          (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" +
          _Symbol + "_" + (string)StrategyMagic(strategy));
  }

//+------------------------------------------------------------------+
bool IsBatchLocked(const ENUM_STRATEGY_ID strategy)
  {
   string key = BatchLockKey(strategy);
   return(GlobalVariableCheck(key) && GlobalVariableGet(key) > 0.5);
  }

//+------------------------------------------------------------------+
void SetBatchLocked(const ENUM_STRATEGY_ID strategy,const bool locked)
  {
   string key = BatchLockKey(strategy);
   if(locked)
      GlobalVariableSet(key,1.0);
   else if(GlobalVariableCheck(key))
      GlobalVariableDel(key);
  }

//+------------------------------------------------------------------+
bool IsOurMagic(const ulong magic)
  {
   return(magic == TrendMagicNumber || magic == RangeMagicNumber);
  }

//+------------------------------------------------------------------+
int CountStrategyPositions(const ENUM_STRATEGY_ID strategy)
  {
   int count = 0;
   ulong magic = StrategyMagic(strategy);
   for(int i=PositionsTotal()-1; i>=0; --i)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == magic)
         count++;
     }
   return(count);
  }

//+------------------------------------------------------------------+
int CountAllOurPositions()
  {
   int count = 0;
   for(int i=PositionsTotal()-1; i>=0; --i)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      ulong magic = (ulong)PositionGetInteger(POSITION_MAGIC);
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && IsOurMagic(magic))
         count++;
     }
   return(count);
  }

//+------------------------------------------------------------------+
void ReconcileBatchLock(const ENUM_STRATEGY_ID strategy)
  {
   int count = CountStrategyPositions(strategy);
   if(count == 0)
      SetBatchLocked(strategy,false);
   else if(count >= MaxPositionsPerStrategy)
      SetBatchLocked(strategy,true);
   // With one remaining position an existing lock must stay locked. This
   // prevents refilling a batch after one of its two layers has closed.
  }

//+------------------------------------------------------------------+
bool ValidateInputs()
  {
   if(!EnableTrend && !EnableRange)
     {
      Print("At least one strategy must be enabled.");
      return(false);
     }
   if(TrendMagicNumber == 0 || RangeMagicNumber == 0 ||
      TrendMagicNumber == RangeMagicNumber)
     {
      Print("Trend and range magic numbers must be non-zero and different.");
      return(false);
     }
   if(TrendMomentumBars < 1 || TrendMomentumATR <= 0.0 ||
      TrendEMAPeriod < 2 || TrendATRPeriod < 2 ||
      TrendStopATR <= 0.0 || TrendRewardRisk <= 0.0 ||
      TrendMaxHoldingBars < 1)
     {
      Print("Invalid trend-strategy inputs.");
      return(false);
     }
   if(RangeWindow < 2 || RangeZEntry <= 0.0 || RangeRSIPeriod < 2 ||
      RangeRSILongMaximum <= 0.0 || RangeRSILongMaximum >= 50.0 ||
      RangeRSIShortMinimum <= 50.0 || RangeRSIShortMinimum >= 100.0 ||
      RangeFastEMA < 2 || RangeSlowEMA <= RangeFastEMA ||
      RangeMaxTrendGapATR <= 0.0 || RangeStopATR <= 0.0 ||
      RangeRewardRisk <= 0.0 || RangeMaxHoldingBars < 1)
     {
      Print("Invalid range-strategy inputs.");
      return(false);
     }
   if(RiskPercentPerPosition <= 0.0 || RiskPercentPerPosition > 5.0 ||
      MaxPositionsPerStrategy < 1 || MaxPositionsPerStrategy > 2 ||
      MaxTotalPositions < 1 || MaxTotalPositions > 4 ||
      MaxTotalPositions < MaxPositionsPerStrategy ||
      DeviationPoints < 0)
     {
      Print("Invalid risk/execution inputs.");
      return(false);
     }
   return(true);
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(!ValidateInputs())
      return(INIT_PARAMETERS_INCORRECT);

   if(MaxTotalPositions > 1 &&
      AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
     {
      Print("This EA requires an MT5 hedging account for multiple positions.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   atr_handle       = iATR(_Symbol,PERIOD_M15,TrendATRPeriod);
   trend_ema_handle = iMA(_Symbol,PERIOD_M15,TrendEMAPeriod,0,MODE_EMA,PRICE_CLOSE);
   fast_ema_handle  = iMA(_Symbol,PERIOD_M15,RangeFastEMA,0,MODE_EMA,PRICE_CLOSE);
   slow_ema_handle  = iMA(_Symbol,PERIOD_M15,RangeSlowEMA,0,MODE_EMA,PRICE_CLOSE);
   rsi_handle       = iRSI(_Symbol,PERIOD_M15,RangeRSIPeriod,PRICE_CLOSE);

   if(atr_handle == INVALID_HANDLE || trend_ema_handle == INVALID_HANDLE ||
      fast_ema_handle == INVALID_HANDLE || slow_ema_handle == INVALID_HANDLE ||
      rsi_handle == INVALID_HANDLE)
     {
      Print("Unable to create indicator handles. Error: ",GetLastError());
      return(INIT_FAILED);
     }

   last_bar_time = iTime(_Symbol,PERIOD_M15,0);
   ReconcileBatchLock(STRATEGY_TREND);
   ReconcileBatchLock(STRATEGY_RANGE);

   Print("XAU M15 Dual Regime initialized on ",_Symbol,
         ". It runs 24 hours and always calculates from PERIOD_M15.");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(atr_handle != INVALID_HANDLE)
      IndicatorRelease(atr_handle);
   if(trend_ema_handle != INVALID_HANDLE)
      IndicatorRelease(trend_ema_handle);
   if(fast_ema_handle != INVALID_HANDLE)
      IndicatorRelease(fast_ema_handle);
   if(slow_ema_handle != INVALID_HANDLE)
      IndicatorRelease(slow_ema_handle);
   if(rsi_handle != INVALID_HANDLE)
      IndicatorRelease(rsi_handle);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   datetime current_bar_time = iTime(_Symbol,PERIOD_M15,0);
   if(current_bar_time <= 0 || current_bar_time == last_bar_time)
      return;

   last_bar_time = current_bar_time;
   ManageTimeExits();
   ReconcileBatchLock(STRATEGY_TREND);
   ReconcileBatchLock(STRATEGY_RANGE);
   EvaluateNewBarEntries();
  }

//+------------------------------------------------------------------+
bool ReadIndicatorValue(const int handle,const int shift,double &value)
  {
   double buffer[1];
   ResetLastError();
   if(CopyBuffer(handle,0,shift,1,buffer) != 1)
     {
      Print("CopyBuffer failed for handle ",handle,
            ", shift ",shift,". Error: ",GetLastError());
      return(false);
     }
   value = buffer[0];
   return(MathIsValidNumber(value));
  }

//+------------------------------------------------------------------+
bool CalculateRollingZ(const MqlRates &rates[],const int start_shift,
                       const int window,double &z_value)
  {
   double sum = 0.0;
   for(int i=0; i<window; ++i)
      sum += rates[start_shift+i].close;
   double mean = sum/(double)window;

   double variance_sum = 0.0;
   for(int i=0; i<window; ++i)
     {
      double difference = rates[start_shift+i].close-mean;
      variance_sum += difference*difference;
     }
   double deviation = MathSqrt(variance_sum/(double)window);
   if(!MathIsValidNumber(deviation) || deviation <= 0.0)
      return(false);

   z_value = (rates[start_shift].close-mean)/deviation;
   return(MathIsValidNumber(z_value));
  }

//+------------------------------------------------------------------+
bool DirectionModeAllows(const ENUM_ORDER_TYPE order_type)
  {
   if(DirectionMode == DIRECTION_LONG_ONLY && order_type != ORDER_TYPE_BUY)
      return(false);
   if(DirectionMode == DIRECTION_SHORT_ONLY && order_type != ORDER_TYPE_SELL)
      return(false);
   return(true);
  }

//+------------------------------------------------------------------+
bool StrategyDirectionAllowed(const ENUM_STRATEGY_ID strategy,
                              const ENUM_ORDER_TYPE order_type)
  {
   ulong magic = StrategyMagic(strategy);
   for(int i=PositionsTotal()-1; i>=0; --i)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC) != magic)
         continue;

      ENUM_POSITION_TYPE position_type =
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if(order_type == ORDER_TYPE_BUY && position_type != POSITION_TYPE_BUY)
         return(false);
      if(order_type == ORDER_TYPE_SELL && position_type != POSITION_TYPE_SELL)
         return(false);
     }
   return(true);
  }

//+------------------------------------------------------------------+
void EvaluateNewBarEntries()
  {
   int required = MathMax(TrendMomentumBars+3,RangeWindow+2);
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   ResetLastError();
   if(CopyRates(_Symbol,PERIOD_M15,0,required,rates) != required)
     {
      Print("Not enough M15 rates yet. Error: ",GetLastError());
      return;
     }

   double atr_1,atr_2,trend_ema_1,fast_ema_1,slow_ema_1,rsi_1;
   if(!ReadIndicatorValue(atr_handle,1,atr_1) ||
      !ReadIndicatorValue(atr_handle,2,atr_2) ||
      !ReadIndicatorValue(trend_ema_handle,1,trend_ema_1) ||
      !ReadIndicatorValue(fast_ema_handle,1,fast_ema_1) ||
      !ReadIndicatorValue(slow_ema_handle,1,slow_ema_1) ||
      !ReadIndicatorValue(rsi_handle,1,rsi_1) ||
      atr_1 <= 0.0 || atr_2 <= 0.0)
      return;

   ENUM_ORDER_TYPE trend_order = ORDER_TYPE_BUY;
   bool trend_signal = false;
   if(EnableTrend)
     {
      double momentum_now =
         (rates[1].close-rates[1+TrendMomentumBars].close)/atr_1;
      double momentum_previous =
         (rates[2].close-rates[2+TrendMomentumBars].close)/atr_2;

      bool trend_long =
         momentum_now > TrendMomentumATR &&
         momentum_previous <= TrendMomentumATR &&
         rates[1].close > trend_ema_1;
      bool trend_short =
         momentum_now < -TrendMomentumATR &&
         momentum_previous >= -TrendMomentumATR &&
         rates[1].close < trend_ema_1;

      if(trend_long || trend_short)
        {
         trend_order = trend_long ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
         trend_signal = DirectionModeAllows(trend_order);
        }
     }

   ENUM_ORDER_TYPE range_order = ORDER_TYPE_BUY;
   bool range_signal = false;
   if(EnableRange)
     {
      double z_now,z_previous;
      if(CalculateRollingZ(rates,1,RangeWindow,z_now) &&
         CalculateRollingZ(rates,2,RangeWindow,z_previous))
        {
         double trend_gap_atr = MathAbs(fast_ema_1-slow_ema_1)/atr_1;
         bool range_long =
            z_now < -RangeZEntry && z_previous >= -RangeZEntry &&
            rsi_1 < RangeRSILongMaximum &&
            trend_gap_atr < RangeMaxTrendGapATR;
         bool range_short =
            z_now > RangeZEntry && z_previous <= RangeZEntry &&
            rsi_1 > RangeRSIShortMinimum &&
            trend_gap_atr < RangeMaxTrendGapATR;

         if(range_long || range_short)
           {
            range_order = range_long ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
            range_signal = DirectionModeAllows(range_order);
           }
        }
     }

   // The two systems are intentionally independent. They may hold opposite
   // directions at the same time, but each system may only layer its own side.
   if(trend_signal)
      TryOpenStrategy(STRATEGY_TREND,trend_order,atr_1,
                      TrendStopATR,TrendRewardRisk);
   if(range_signal)
      TryOpenStrategy(STRATEGY_RANGE,range_order,atr_1,
                      RangeStopATR,RangeRewardRisk);
  }

//+------------------------------------------------------------------+
double PriceTickSize()
  {
   double tick_size = SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0.0)
      tick_size = SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   return(tick_size);
  }

//+------------------------------------------------------------------+
double NormalizePriceDown(const double price)
  {
   double tick_size = PriceTickSize();
   if(tick_size <= 0.0)
      return(NormalizeDouble(price,_Digits));
   return(NormalizeDouble(MathFloor(price/tick_size+1e-10)*tick_size,_Digits));
  }

//+------------------------------------------------------------------+
double NormalizePriceUp(const double price)
  {
   double tick_size = PriceTickSize();
   if(tick_size <= 0.0)
      return(NormalizeDouble(price,_Digits));
   return(NormalizeDouble(MathCeil(price/tick_size-1e-10)*tick_size,_Digits));
  }

//+------------------------------------------------------------------+
void TryOpenStrategy(const ENUM_STRATEGY_ID strategy,
                     const ENUM_ORDER_TYPE order_type,
                     const double atr_value,
                     const double stop_atr,
                     const double reward_risk)
  {
   int strategy_positions = CountStrategyPositions(strategy);
   int total_positions = CountAllOurPositions();
   if(strategy_positions >= MaxPositionsPerStrategy ||
      total_positions >= MaxTotalPositions ||
      IsBatchLocked(strategy))
      return;

   if(!StrategyDirectionAllowed(strategy,order_type))
     {
      Print(StrategyLabel(strategy),
            " opposite signal skipped while its existing layer is open.");
      return;
     }

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED))
     {
      Print("Automated trading is not allowed by terminal or EA settings.");
      return;
     }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.ask <= 0.0 || tick.bid <= 0.0)
      return;

   double spread = tick.ask-tick.bid;
   if(MaxSpreadPrice > 0.0 && spread > MaxSpreadPrice)
     {
      Print(StrategyLabel(strategy)," signal skipped: spread ",
            DoubleToString(spread,_Digits)," exceeds ",
            DoubleToString(MaxSpreadPrice,_Digits));
      return;
     }

   double point = SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double minimum_distance =
      (double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   minimum_distance = MathMax(minimum_distance,PriceTickSize());

   double entry = order_type == ORDER_TYPE_BUY ? tick.ask : tick.bid;
   double risk_distance = stop_atr*atr_value;
   double stop,target;
   if(order_type == ORDER_TYPE_BUY)
     {
      stop = MathMin(entry-risk_distance,tick.bid-minimum_distance);
      target = MathMax(entry+risk_distance*reward_risk,
                       tick.bid+minimum_distance);
      stop = NormalizePriceDown(stop);
      target = NormalizePriceUp(target);
     }
   else
     {
      stop = MathMax(entry+risk_distance,tick.ask+minimum_distance);
      target = MathMin(entry-risk_distance*reward_risk,
                       tick.ask-minimum_distance);
      stop = NormalizePriceUp(stop);
      target = NormalizePriceDown(target);
     }

   if(stop <= 0.0 || target <= 0.0 ||
      (order_type == ORDER_TYPE_BUY && (stop >= entry || target <= entry)) ||
      (order_type == ORDER_TYPE_SELL && (stop <= entry || target >= entry)))
     {
      Print("Invalid normalized stop/target for ",StrategyLabel(strategy));
      return;
     }

   double volume = CalculateRiskVolume(order_type,entry,stop);
   if(volume <= 0.0)
      return;

   int layer_number = strategy_positions+1;
   if(OpenMarketPosition(strategy,order_type,volume,stop,target,layer_number))
     {
      if(layer_number >= MaxPositionsPerStrategy)
         SetBatchLocked(strategy,true);
     }
  }

//+------------------------------------------------------------------+
double CalculateRiskVolume(const ENUM_ORDER_TYPE order_type,
                           const double entry,const double stop)
  {
   double risk_money =
      AccountInfoDouble(ACCOUNT_BALANCE)*RiskPercentPerPosition/100.0;
   double loss_one_lot = 0.0;
   ResetLastError();
   if(risk_money <= 0.0 ||
      !OrderCalcProfit(order_type,_Symbol,1.0,entry,stop,loss_one_lot) ||
      loss_one_lot == 0.0)
     {
      Print("Unable to calculate risk volume. Error: ",GetLastError());
      return(0.0);
     }

   double volume_min = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double volume_max = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double volume_step = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double volume_limit = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_LIMIT);
   if(volume_min <= 0.0 || volume_max <= 0.0 || volume_step <= 0.0)
     {
      Print("Broker returned invalid volume specifications.");
      return(0.0);
     }

   double raw_volume = risk_money/MathAbs(loss_one_lot);
   double allowed_max = volume_max;
   if(volume_limit > 0.0)
      allowed_max = MathMin(allowed_max,volume_limit);
   double volume = MathFloor(raw_volume/volume_step+1e-9)*volume_step;
   volume = MathMin(volume,allowed_max);

   if(volume < volume_min)
     {
      Print("Trade skipped: risk-based volume ",DoubleToString(raw_volume,8),
            " is below broker minimum ",DoubleToString(volume_min,8));
      return(0.0);
     }

   int volume_digits = 0;
   double step_check = volume_step;
   while(volume_digits < 8 &&
         MathAbs(step_check-MathRound(step_check)) > 1e-9)
     {
      step_check *= 10.0;
      volume_digits++;
     }
   return(NormalizeDouble(volume,volume_digits));
  }

//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode()
  {
   long mode = 0;
   if(!SymbolInfoInteger(_Symbol,SYMBOL_FILLING_MODE,mode))
      return(ORDER_FILLING_FOK);
   if((mode & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return(ORDER_FILLING_IOC);
   if((mode & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return(ORDER_FILLING_FOK);
   return(ORDER_FILLING_RETURN);
  }

//+------------------------------------------------------------------+
bool TradeRetcodeOK(const uint retcode)
  {
   return(retcode == TRADE_RETCODE_DONE ||
          retcode == TRADE_RETCODE_PLACED ||
          retcode == TRADE_RETCODE_DONE_PARTIAL);
  }

//+------------------------------------------------------------------+
bool OpenMarketPosition(const ENUM_STRATEGY_ID strategy,
                        const ENUM_ORDER_TYPE order_type,
                        const double volume,const double stop,
                        const double target,const int layer_number)
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick))
      return(false);

   MqlTradeRequest request = {};
   MqlTradeCheckResult check = {};
   MqlTradeResult result = {};
   request.action       = TRADE_ACTION_DEAL;
   request.magic        = StrategyMagic(strategy);
   request.symbol       = _Symbol;
   request.volume       = volume;
   request.type         = order_type;
   request.price        = order_type == ORDER_TYPE_BUY ? tick.ask : tick.bid;
   request.sl           = stop;
   request.tp           = target;
   request.deviation    = (ulong)DeviationPoints;
   request.type_filling = GetFillingMode();
   request.type_time    = ORDER_TIME_GTC;
   request.comment      = "M15 "+StrategyLabel(strategy)+
                          " L"+(string)layer_number;

   ResetLastError();
   if(!OrderCheck(request,check))
     {
      Print(StrategyLabel(strategy)," OrderCheck failed. Error: ",
            GetLastError(),", retcode: ",check.retcode,
            ", comment: ",check.comment);
      return(false);
     }

   ResetLastError();
   if(!OrderSend(request,result))
     {
      Print(StrategyLabel(strategy)," OrderSend failed. Error: ",
            GetLastError(),", retcode: ",result.retcode,
            ", comment: ",result.comment);
      return(false);
     }
   if(!TradeRetcodeOK(result.retcode))
     {
      Print(StrategyLabel(strategy)," entry rejected. Retcode: ",
            result.retcode,", comment: ",result.comment);
      return(false);
     }

   Print(StrategyLabel(strategy)," layer ",layer_number,
         " opened. Volume=",DoubleToString(volume,8),
         ", SL=",DoubleToString(stop,_Digits),
         ", TP=",DoubleToString(target,_Digits));
   return(true);
  }

//+------------------------------------------------------------------+
void ManageTimeExits()
  {
   ulong tickets[];
   ArrayResize(tickets,PositionsTotal());
   int close_count = 0;

   for(int i=PositionsTotal()-1; i>=0; --i)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      ulong magic = (ulong)PositionGetInteger(POSITION_MAGIC);
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || !IsOurMagic(magic))
         continue;

      ENUM_STRATEGY_ID strategy =
         magic == TrendMagicNumber ? STRATEGY_TREND : STRATEGY_RANGE;
      datetime entry_time = (datetime)PositionGetInteger(POSITION_TIME);
      int bars_held = iBarShift(_Symbol,PERIOD_M15,entry_time,false);
      if(bars_held >= StrategyMaxHoldingBars(strategy))
         tickets[close_count++] = ticket;
     }

   for(int i=0; i<close_count; ++i)
     {
      if(!CloseOurPosition(tickets[i]))
         Print("Time exit failed for ticket ",tickets[i]);
     }
  }

//+------------------------------------------------------------------+
bool CloseOurPosition(const ulong ticket)
  {
   if(!PositionSelectByTicket(ticket))
      return(false);

   ulong magic = (ulong)PositionGetInteger(POSITION_MAGIC);
   if(PositionGetString(POSITION_SYMBOL) != _Symbol || !IsOurMagic(magic))
      return(false);

   ENUM_POSITION_TYPE position_type =
      (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   double volume = PositionGetDouble(POSITION_VOLUME);
   if(volume <= 0.0)
      return(false);

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick))
      return(false);

   ENUM_ORDER_TYPE close_type =
      position_type == POSITION_TYPE_BUY ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   MqlTradeRequest request = {};
   MqlTradeCheckResult check = {};
   MqlTradeResult result = {};
   request.action       = TRADE_ACTION_DEAL;
   request.magic        = magic;
   request.position     = ticket;
   request.symbol       = _Symbol;
   request.volume       = volume;
   request.type         = close_type;
   request.price        = close_type == ORDER_TYPE_BUY ? tick.ask : tick.bid;
   request.deviation    = (ulong)DeviationPoints;
   request.type_filling = GetFillingMode();
   request.type_time    = ORDER_TIME_GTC;
   request.comment      = "M15 max-bars exit";

   ResetLastError();
   if(!OrderCheck(request,check))
     {
      Print("Close OrderCheck failed for ticket ",ticket,
            ". Error: ",GetLastError(),", retcode: ",check.retcode,
            ", comment: ",check.comment);
      return(false);
     }

   ResetLastError();
   if(!OrderSend(request,result))
     {
      Print("Close OrderSend failed for ticket ",ticket,
            ". Error: ",GetLastError(),", retcode: ",result.retcode,
            ", comment: ",result.comment);
      return(false);
     }
   if(!TradeRetcodeOK(result.retcode))
     {
      Print("Close rejected for ticket ",ticket,". Retcode: ",
            result.retcode,", comment: ",result.comment);
      return(false);
     }
   return(true);
  }
//+------------------------------------------------------------------+
