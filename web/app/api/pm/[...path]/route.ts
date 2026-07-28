// FastAPI 프록시 — 게이트(미들웨어) 통과 요청만 여기 도달한다.
import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.PM_API_URL ?? "http://127.0.0.1:8787";

async function proxy(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  const target = new URL(`${API_BASE}/api/${path.join("/")}`);
  request.nextUrl.searchParams.forEach((value, key) => target.searchParams.set(key, value));

  const init: RequestInit = { method: request.method };
  if (request.method !== "GET") {
    // 멀티파트(파일 업로드) 보존을 위해 바이너리 그대로 전달 — text()는 PNG를 깨뜨린다
    init.body = Buffer.from(await request.arrayBuffer());
    const contentType = request.headers.get("content-type");
    if (contentType) init.headers = { "content-type": contentType };
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return NextResponse.json(
      { error: "생성 서버(piemgmaker serve)에 연결할 수 없습니다" },
      { status: 502 },
    );
  }
  const headers = new Headers();
  for (const name of ["content-type", "content-disposition"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers });
}

export { proxy as GET, proxy as POST };
