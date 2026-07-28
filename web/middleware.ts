import { NextRequest, NextResponse } from "next/server";
import { GATE_COOKIE, verifyGateCookie } from "@/lib/gate";

export async function middleware(request: NextRequest) {
  const ok = await verifyGateCookie(request.cookies.get(GATE_COOKIE)?.value);
  if (ok) return NextResponse.next();
  if (request.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "게이트 인증이 필요합니다" }, { status: 401 });
  }
  const gate = request.nextUrl.clone();
  gate.pathname = "/gate";
  gate.search = "";
  return NextResponse.redirect(gate);
}

export const config = {
  // /gate·게이트 API·정적 자원은 예외
  matcher: ["/((?!gate|api/gate|_next|favicon.ico|preview-bg.png).*)"],
};
