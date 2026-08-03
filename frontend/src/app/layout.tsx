import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI PR Review Agent",
  description: "Review pipeline dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">
            AI PR Review Agent
          </Link>
          <nav>
            <Link href="/">Reviews</Link>
            <Link href="/hitl">HITL Queue</Link>
          </nav>
        </header>
        <main>{children}</main>
        <footer className="footer">
          master · M15 dashboard · gates: pytest + mypy + check_deps + canary
        </footer>
      </body>
    </html>
  );
}
