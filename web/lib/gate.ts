// 게이트 쿠키 서명·검증 — Edge(미들웨어)와 Node(라우트) 양쪽에서 동작해야 하므로 WebCrypto(HMAC-SHA256)만 사용한다.

export const GATE_COOKIE = "pm_gate";
export const GATE_TTL_MS = 12 * 60 * 60 * 1000; // 세션 12h

function password(): string {
  return process.env.GATE_PASSWORD ?? "0000";
}

// Edge 런타임 호환 — crypto.timingSafeEqual을 쓸 수 없어 XOR 누적으로 상수시간 비교한다
function constantTimeEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder();
  const aBytes = encoder.encode(a);
  const bBytes = encoder.encode(b);
  if (aBytes.length !== bBytes.length) return false;
  let diff = 0;
  for (let i = 0; i < aBytes.length; i++) {
    diff |= aBytes[i] ^ bBytes[i];
  }
  return diff === 0;
}

async function hmac(message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(`pm-gate-key:${password()}`),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

export async function signGateCookie(now: number = Date.now()): Promise<string> {
  const expires = now + GATE_TTL_MS;
  return `${expires}.${await hmac(`pm-gate:${expires}`)}`;
}

export async function verifyGateCookie(value: string | undefined, now: number = Date.now()): Promise<boolean> {
  if (!value) return false;
  const [expiresRaw, sig] = value.split(".");
  const expires = Number(expiresRaw);
  if (!Number.isFinite(expires) || expires < now || !sig) return false;
  return constantTimeEqual(await hmac(`pm-gate:${expires}`), sig);
}

export function checkPassword(input: string): boolean {
  return constantTimeEqual(input, password());
}
