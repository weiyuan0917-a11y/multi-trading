export type ResearchStatus = {
  has_snapshot?: boolean;
  generated_at?: string | null;
  market?: string | null;
  kline?: string | null;
  top_n?: number | null;
  version?: string | null;
  data_providers?: {
    primary?: string;
    openbb_enabled?: boolean;
    openbb_connected?: boolean;
    openbb_base_url?: string;
    openbb_modules?: Record<string, boolean>;
    openbb_macro_available?: boolean;
    openbb_macro_regime?: string;
    openbb_sec_available?: boolean;
    openbb_sec_symbols?: number;
    openbb_etf_available?: boolean;
    openbb_etf_symbols?: number;
    openbb_derivatives_available?: boolean;
    cn_public_data?: CnPublicDataStatus;
    tradingagents_enabled?: boolean;
    tradingagents_provider?: string | null;
    tradingagents_data_source?: string | null;
    tradingagents_effective_data_source?: string | null;
    provider_status_error?: string;
  };
  task_queue?: {
    queued?: number;
    running?: number;
    active?: number;
    max_pending?: number;
    queued_by_type?: Record<string, number>;
    running_by_type?: Record<string, number>;
    active_tasks?: Array<{
      task_id?: string;
      task_type?: "research" | "strategy_matrix" | "ml_matrix" | string;
      status?: string;
      created_at?: string | null;
      started_at?: string | null;
      progress_pct?: number;
      progress_stage?: string;
      progress_text?: string;
      progress_updated_at?: string | null;
      queue_position?: number;
      queue_ahead?: number;
    }>;
  };
};

export type ResearchSnapshot = {
  has_snapshot?: boolean;
  snapshot?: {
    version?: string;
    generated_at?: string;
    market?: string;
    kline?: string;
    top_n?: number;
    research_options?: {
      openbb?: boolean;
      tradingagents?: boolean;
      pair_backtest?: boolean;
      ml_diagnostics?: boolean;
    };
    pair_pool_used?: Array<{
      long_symbol?: string;
      short_symbol?: string;
    }>;
    pair_pool_size?: number;
    data_providers?: {
      primary?: string;
      openbb_enabled?: boolean;
      openbb_connected?: boolean;
      openbb_base_url?: string;
      openbb_modules?: Record<string, boolean>;
      openbb_macro_available?: boolean;
      openbb_macro_regime?: string;
      openbb_sec_available?: boolean;
      openbb_sec_symbols?: number;
      openbb_etf_available?: boolean;
      openbb_etf_symbols?: number;
      openbb_derivatives_available?: boolean;
      cn_public_data?: CnPublicDataStatus;
    };
    external_research?: {
      market_regime?: {
        available?: boolean;
        regime?: string;
        confidence?: number;
        as_of?: string;
        symbol?: string;
        features?: {
          ret_20?: number;
          vol_z?: number;
        };
        note?: string;
        reason?: string;
      };
      macro_regime?: {
        available?: boolean;
        regime?: string;
        confidence?: number;
        risk_score?: number;
        as_of?: string;
        features?: {
          dgs10?: number | null;
          dgs2?: number | null;
          spread_10y2y?: number | null;
          dgs10_change_60?: number | null;
          fedfunds?: number | null;
          financial_stress?: number | null;
          unrate_change_60?: number | null;
          cpi_yoy?: number | null;
        };
        reasons?: string[];
        note?: string;
        reason?: string;
      };
      macro_indicators?: Record<string, any>;
      sec_disclosures?: Array<{
        symbol?: string;
        available?: boolean;
        important_count?: number;
        filings?: {
          count?: number;
          important_count?: number;
          items?: Array<{
            filing_date?: string;
            report_type?: string;
            description?: string;
            report_url?: string;
            filing_detail_url?: string;
            important?: boolean;
          }>;
        };
        insider_trading?: {
          count?: number;
          important_count?: number;
          items?: Array<{
            filing_date?: string;
            transaction_date?: string;
            owner_name?: string;
            owner_title?: string;
            acquisition_or_disposition?: string;
            securities_transacted?: number | null;
            transaction_value?: number | null;
            filing_url?: string;
            important?: boolean;
          }>;
        };
      }>;
      etf_exposures?: Array<{
        symbol?: string;
        available?: boolean;
        info?: {
          available?: boolean;
          provider?: string;
          info?: {
            symbol?: string;
            name?: string;
            category?: string;
            total_assets?: number;
            trailing_pe?: number | null;
            dividend_yield?: number | null;
            return_ytd?: number | null;
            beta_3y_avg?: number | null;
            ma_50d?: number | null;
            ma_200d?: number | null;
          };
        };
        sectors?: {
          available?: boolean;
          count?: number;
          reason?: string;
          items?: Array<{ symbol?: string; name?: string; weight?: number | null }>;
        };
        holdings?: {
          available?: boolean;
          count?: number;
          reason?: string;
          items?: Array<{ symbol?: string; name?: string; weight?: number | null }>;
        };
      }>;
      derivatives_risk?: {
        symbol?: string;
        available?: boolean;
        options?: {
          available?: boolean;
          underlying_price?: number | null;
          total_contracts?: number;
          nearest_expiration?: string;
          nearest_dte?: number | null;
          near_dte_contracts?: number;
          put_call_volume_ratio?: number | null;
          put_call_oi_ratio?: number | null;
          top_volume?: Array<{
            contract_symbol?: string;
            expiration?: string;
            dte?: number;
            strike?: number;
            option_type?: string;
            volume?: number;
            open_interest?: number;
            implied_volatility?: number;
          }>;
        };
        futures_curve?: {
          available?: boolean;
          symbol?: string;
          count?: number;
          curve_spread?: number | null;
          items?: Array<{ expiration?: string; price?: number }>;
        };
        cot?: {
          available?: boolean;
          date?: string;
          market?: string;
          open_interest?: number | null;
          leveraged_money_net?: number | null;
          asset_manager_net?: number | null;
          leveraged_money_net_oi?: number | null;
          asset_manager_net_oi?: number | null;
        };
      };
      symbol_factors?: Array<{
        symbol?: string;
        source?: string;
        available?: boolean;
        volatility_30d?: number | null;
        sentiment_score?: number | null;
      }>;
      tradingagents_insights?: Array<{
        symbol?: string;
        request_symbol?: string;
        market?: string;
        source?: string;
        available?: boolean;
        action?: "buy" | "sell" | "hold" | string;
        confidence?: number;
        decision_text?: string;
        reason?: string;
        error?: string;
        generated_at?: string;
        timeout_seconds?: number;
        research_report_markdown?: string;
        stage_reports?: Record<string, string>;
      }>;
    };
    strong_stocks?: Array<{
      symbol?: string;
      last?: number;
      change_pct?: number;
      price_type?: string;
      price_source?: string;
      ret5_pct?: number;
      ret20_pct?: number;
      strength_score?: number;
    }>;
    allocation_plan?: Array<{
      symbol?: string;
      weight_raw?: number;
      weight?: number;
      weight_pre_external_risk?: number;
      external_risk_multiplier?: number;
      strength_score?: number;
      price_type?: string;
    }>;
    strategy_rankings?: Array<{
      symbol?: string;
      best_strategy?: {
        strategy?: string;
        strategy_label?: string;
        composite_score?: number;
        composite_score_raw?: number;
        regime_multiplier?: number;
        net_return_pct?: number;
        sharpe_ratio?: number;
        max_drawdown_pct?: number;
      };
    }>;
    regime_gating?: {
      applied?: boolean;
      regime_name?: string;
      regime_confidence?: number;
      max_single_ratio?: number;
      target_gross_exposure?: number;
      effective_exposure?: number;
      formula?: string;
    };
    external_risk_notes?: Array<{
      severity?: "info" | "warn" | "risk" | string;
      source?: string;
      title?: string;
      message?: string;
      symbols?: string[];
      reasons?: string[];
      score?: number;
    }>;
    external_risk_gating?: {
      enabled?: boolean;
      applied?: boolean;
      risk_count?: number;
      warn_count?: number;
      reduction?: number;
      multiplier?: number;
      max_reduction?: number;
      effective_exposure_before?: number;
      effective_exposure_after?: number;
      reason?: string;
      formula?: string;
    };
    factor_gating?: {
      applied?: boolean;
      available_symbols?: number;
      total_symbols?: number;
      formula?: string;
    };
    agent_gating?: {
      applied?: boolean;
      weight?: number;
      available_symbols?: number;
      applied_symbols?: number;
      buy_signals?: number;
      sell_signals?: number;
      hold_signals?: number;
      formula?: string;
    };
    factor_ab_report?: {
      generated_at?: string;
      summary?: {
        top5_baseline?: string[];
        top5_with_factor?: string[];
        overlap_count?: number;
        overlap_symbols?: string[];
        entered_symbols?: string[];
        exited_symbols?: string[];
        avg_best_score_baseline?: number;
        avg_best_score_with_factor?: number;
        avg_best_score_delta?: number;
        allocation_turnover?: number;
      };
      items?: Array<{
        symbol?: string;
        score_baseline?: number | null;
        score_with_factor?: number | null;
        score_delta?: number | null;
        factor_multiplier?: number;
        weight_baseline?: number;
        weight_with_factor?: number;
        weight_delta?: number;
      }>;
    };
    ml_diagnostics?: {
      enabled?: boolean;
      reason?: string;
      settings?: {
        requested_model_type?: string;
        horizon_days?: number;
        train_ratio?: number;
        walk_forward_windows?: number;
        transaction_cost_bps?: number;
        feature_count?: number;
      };
      dataset?: {
        symbols_requested?: number;
        symbols_used?: number;
        bars_total?: number;
        samples?: number;
      };
      label_distribution?: {
        positive?: number;
        negative?: number;
        positive_ratio?: number;
      };
      net_future_ret_summary?: {
        mean?: number;
        p10?: number;
        p25?: number;
        p50?: number;
        p75?: number;
        p90?: number;
      };
      models?: Array<{
        model_name?: string;
        latest_up_probability?: number | null;
        metric_score?: number;
        walk_forward_coverage?: number;
        walk_forward?: {
          accuracy?: number;
          precision?: number;
          recall?: number;
          coverage?: number;
          windows?: number;
          oos_samples?: number;
        };
      }>;
    };
    pair_backtest?: Record<string, any>;
  };
};

export type CnPublicDataStatus = {
  schema?: string;
  ready?: boolean;
  quote_ready?: number;
  quote_enabled?: number;
  valuation_ready?: boolean;
  broker_required?: boolean;
  openbb_required?: boolean;
  research_cache?: {
    available?: boolean;
    latest_symbol?: string;
    latest_at?: string;
  };
  latest_fundamental_period?: string | null;
  latest_news_items?: number | null;
  latest_event_items?: number | null;
  providers?: Array<{
    id?: string;
    name?: string;
    enabled?: boolean;
    ready?: boolean;
    status_text?: string;
    priority?: number;
  }>;
  latest_news_diagnostics?: Array<{
    source?: string;
    count?: number;
    ok?: boolean;
    error?: string;
  }>;
  latest_fundamental_diagnostics?: Array<{
    source?: string;
    count?: number;
    ok?: boolean;
    error?: string;
  }>;
};

export type ModelCompareResult = {
  count?: number;
  items?: Array<{
    model_name?: string;
    runs?: number;
    avg_score?: number;
    best_score?: number;
    avg_accuracy?: number;
  }>;
};

export type StrategyMatrixItem = {
  strategy?: string;
  strategy_label?: string;
  strategy_params?: Record<string, unknown>;
  top_symbols?: Array<{
    symbol?: string;
    net_return_pct?: number;
    max_drawdown_pct?: number;
    sharpe_ratio?: number;
    win_rate_pct?: number;
    trades?: number;
  }>;
  kline?: string;
  backtest_days?: number;
  commission_bps?: number;
  slippage_bps?: number;
  symbols_used?: number;
  symbols_total?: number;
  avg_net_return_pct?: number;
  avg_max_drawdown_pct?: number;
  avg_sharpe_ratio?: number;
  avg_win_rate_pct?: number;
  avg_trades?: number;
  matrix_score?: number;
};

export type StrategyMatrixPayload = {
  generated_at?: string;
  trace_id?: string;
  market?: string;
  ok?: boolean;
  grid_size?: number;
  strategy_count?: number;
  candidate_count?: number;
  symbols?: string[];
  best_balanced?: StrategyMatrixItem | null;
  best_aggressive?: StrategyMatrixItem | null;
  best_defensive?: StrategyMatrixItem | null;
  items?: StrategyMatrixItem[];
};

export type StrategyMatrixResult = {
  has_result?: boolean;
  result?: StrategyMatrixPayload;
};

export type MlMatrixItem = {
  params?: {
    model_type?: "logreg" | "random_forest" | "gbdt";
    ml_threshold?: number;
    ml_horizon_days?: number;
    ml_train_ratio?: number;
    ml_walk_forward_windows?: number;
    transaction_cost_bps?: number;
  };
  metrics?: {
    accuracy?: number;
    precision?: number;
    recall?: number;
    coverage?: number;
    oos_samples?: number;
    windows?: number;
  };
  dataset?: {
    symbols_used?: number;
    symbols_total?: number;
    samples?: number;
  };
  stability?: {
    coverage_std?: number;
    accuracy_std?: number;
    precision_std?: number;
  };
  score?: number;
  pass_constraints?: boolean;
  failed_reasons?: string[];
};

export type MlMatrixPayload = {
  generated_at?: string;
  trace_id?: string;
  market?: string;
  kline?: string;
  ok?: boolean;
  grid_size?: number;
  evaluated_count?: number;
  passed_constraints_count?: number;
  top_n?: number;
  signal_bars_days?: number;
  signal_bars_days_requested?: number;
  /** 后端因特征行数下限自动加长 K 线窗口时的说明 */
  signal_bars_days_note?: string;
  /** 各标的 raw bar 数与探测得到的特征行数（日K=交易日根数） */
  bar_fetch_preflight?: Array<{
    symbol?: string;
    raw_bars?: number;
    feature_rows?: number | null;
    meets_matrix_min?: boolean;
    error?: string;
    feature_error?: string | null;
  }>;
  best_balanced?: MlMatrixItem | null;
  best_high_precision?: MlMatrixItem | null;
  best_high_coverage?: MlMatrixItem | null;
  items?: MlMatrixItem[];
};

export type MlMatrixResult = {
  has_result?: boolean;
  result?: MlMatrixPayload;
};

export type FactorABMarkdownResult = {
  has_report?: boolean;
  generated_at?: string | null;
  markdown?: string;
};
