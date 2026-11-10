import "./globals.css";
import { ChatDock } from "../components/ChatDock";
import { SiteHeader } from "../components/SiteHeader";

export const metadata = { title: "Vault Compass" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ChatDock>
          <SiteHeader />
          {children}
        </ChatDock>
      </body>
    </html>
  );
}
