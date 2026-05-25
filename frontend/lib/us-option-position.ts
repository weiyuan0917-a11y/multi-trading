/**
 * 美股期权（OCC 代码，如 QQQ260410P610000.US）合约乘数：每张对应 100 股名义。
 * 成本 / 市值 / 浮动盈亏应以「张数 × 每股权利金 × 100」为美元口径展示。
 */
export const US_OPTION_CONTRACT_MULTIPLIER = 100;

/** OCC 风格：标的根 + 6 位到期日 + C/P + 行权价数字 + .US */
export function isLikelyUsEquityOptionSymbol(symbol: string | undefined | null): boolean {
  const s = String(symbol || "").trim().toUpperCase();
  return /\d{6}[CP]\d+\.US$/.test(s);
}

export function positionContractMultiplier(symbol: string | undefined | null): number {
  return isLikelyUsEquityOptionSymbol(symbol) ? US_OPTION_CONTRACT_MULTIPLIER : 1;
}
