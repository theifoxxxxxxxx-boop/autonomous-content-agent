import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Autonomous Content Agent",
  description: "Upload images, generate copy, and prepare publish flow",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
