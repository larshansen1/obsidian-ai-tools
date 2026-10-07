import { TopicPageScreen } from "../../../components/TopicPageScreen";
import { isSignalKind, isWindow } from "../../../lib/topicPage";

export default async function TopicPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ signal?: string; window?: string }>;
}) {
  const { id } = await params;
  const { signal, window } = await searchParams;
  return (
    <TopicPageScreen
      id={decodeURIComponent(id)}
      window={isWindow(window) ? window : "30"}
      signal={isSignalKind(signal) ? signal : null}
    />
  );
}
