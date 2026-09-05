import "./globals.css";

export const metadata = {
  title: "MultiTrading Community",
  description: "Research, backtest, and Paper trading",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
