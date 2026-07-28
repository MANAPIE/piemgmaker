"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function GatePage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    const response = await fetch("/api/gate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (response.ok) {
      router.push("/");
      router.refresh();
    } else {
      setError("암호가 올바르지 않습니다");
    }
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-[var(--mat-deep)] px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-3">
          <span className="checker-fine block h-7 w-7 rounded-md ring-1 ring-white/25" aria-hidden />
          <span className="font-mono text-xl font-semibold tracking-tight text-white">
            PIEmgmaker
          </span>
        </div>
        <div className="rounded-xl bg-[var(--card)] p-6 shadow-xl">
          <p className="text-sm text-[var(--ink-soft)]">
            배너 오브젝트 생성 스튜디오. 접속 암호를 입력하세요 — 모든 생성 이력은{" "}
            <b className="text-[var(--ink)]">히스토리</b>에 남습니다.
          </p>
          <form onSubmit={submit} className="mt-4 flex gap-2">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-[var(--line)] px-3 py-2 font-mono text-sm"
              placeholder="접속 암호"
              autoFocus
            />
            <button className="shrink-0 rounded-md bg-[var(--mat)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--mat-deep)]">
              입장
            </button>
          </form>
          {error && <p className="mt-2 text-sm text-[var(--fail)]">{error}</p>}
        </div>
      </div>
    </div>
  );
}
