import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AEGAEON — Agentic Compute",
  description: "Local-first distributed AI coding and compute orchestration",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

