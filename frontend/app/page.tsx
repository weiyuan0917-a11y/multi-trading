"use client";

import { useState } from "react";

const apiBase = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8010";

export default function Home() {
  const [symbol, setSymbol] = useState("AAPL");
  const [result, setResult] = useState("Ready for research.");

  async function research() {
    const response = await fetch(`${apiBase}/research/options/${encodeURIComponent(symbol)}`);
    setResult(JSON.stringify(await response.json(), null, 2));
  }

  async function paperBuy() {
    const quote = await (await fetch(`${apiBase}/market/quote/${encodeURIComponent(symbol)}`)).json();
    const response = await fetch(`${apiBase}/paper/orders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol, asset_class: "stock", side: "buy", quantity: 1,
        reference_price: quote.last, strategy: "community-ui", execution_mode: "paper",
      }),
    });
    setResult(JSON.stringify(await response.json(), null, 2));
  }

  return <main>
    <p className="eyebrow">MultiTrading Community</p>
    <h1>Research and Paper Trading</h1>
    <p className="summary">Stocks, options, backtests and simulated execution. No broker connection is available in this edition.</p>
    <section>
      <label htmlFor="symbol">Symbol</label>
      <input id="symbol" value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} />
      <button onClick={research}>Option Research</button>
      <button className="paper" onClick={paperBuy}>Paper Buy 1 Share</button>
    </section>
    <pre>{result}</pre>
  </main>;
}
