import { NextRequest, NextResponse } from "next/server";
import { GATE_COOKIE, GATE_TTL_MS, checkPassword, signGateCookie } from "@/lib/gate";

export async function POST(request: NextRequest) {
  const { password } = (await request.json().catch(() => ({}))) as { password?: string };
  if (!password || !checkPassword(password)) {
    return NextResponse.json({ error: "암호가 올바르지 않습니다" }, { status: 401 });
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set(GATE_COOKIE, await signGateCookie(), {
    httpOnly: true,
    sameSite: "lax",
    maxAge: GATE_TTL_MS / 1000,
    path: "/",
  });
  return response;
}
