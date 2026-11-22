export const dynamic = "force-dynamic";

export async function GET(request) {
  try {
    const base = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";
    const { search } = new URL(request.url);
    const upstream = await fetch(`${base}/markets${search}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return Response.json(await upstream.json(), { status: upstream.status });
  } catch {
    return Response.json({ error: "The market catalog is unavailable." }, { status: 503 });
  }
}
