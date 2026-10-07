import "./globals.css";
import { ChatDock } from "../components/ChatDock";

export const metadata = { title: "Vault Compass" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ChatDock>{children}</ChatDock>
      </body>
    </html>
  );
}
