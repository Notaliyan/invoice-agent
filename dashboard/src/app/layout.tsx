import type { Metadata } from "next";

import Nav from "@/components/Nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "Invoice Agent",
  description:
    "Upload an invoice and watch a tool-calling agent extract and validate it, then explore the stored results.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <Nav />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
