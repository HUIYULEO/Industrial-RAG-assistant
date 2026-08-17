import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Design Review Console",
  description: "Controlled evidence review for warehouse automation systems.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
