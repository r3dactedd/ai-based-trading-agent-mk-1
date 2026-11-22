export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const base = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";
    const response = await fetch(`${base}/dashboard`, { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error("Dashboard data unavailable");
    return Response.json(await response.json());
  } catch {
    return Response.json({ error: "The trading API is unavailable." }, { status: 503 });
  }
}
