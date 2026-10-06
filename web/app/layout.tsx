export const metadata = { title: "Vault Compass" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui", margin: 0, color: "#1f2328" }}>{children}</body>
    </html>
  );
}
