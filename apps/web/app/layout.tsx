import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Shanghai Home Radar",
  description: "A private decision system for Shanghai second-hand homes.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
