import Link from "next/link";

// Placeholder: the real topic page is a later issue. It exists so "Open topic page" works for every topic (M7).
export default async function TopicPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <main style={{ padding: 24 }}>
      <p>
        <Link href="/">Back to topic map</Link>
      </p>
      <h1>{id}</h1>
      <p>The topic page is not built yet.</p>
    </main>
  );
}
