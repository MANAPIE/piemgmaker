"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import SidebarStatus from "@/components/SidebarStatus";

const NAV = [
  { href: "/", label: "생성하기", hint: "새 오브젝트" },
  { href: "/history", label: "히스토리", hint: "생성 기록" },
  { href: "/assets", label: "자산", hint: "삽입용 라이브러리" },
];

function Wordmark() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      <span className="checker-fine block h-5 w-5 rounded-[4px] ring-1 ring-white/25" aria-hidden />
      <span className="font-mono text-[15px] font-semibold tracking-tight text-white">
        PIEmgmaker
      </span>
    </Link>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  if (pathname.startsWith("/gate")) return null;

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <aside className="flex shrink-0 flex-col bg-[var(--mat-deep)] md:min-h-screen md:w-56">
      <div className="px-5 pb-4 pt-5 md:pb-8 md:pt-7">
        <Wordmark />
      </div>
      <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:pb-0">
        {NAV.map((item) => {
          const active = isActive(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group flex shrink-0 items-baseline justify-between gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                active
                  ? "bg-[var(--paper)] font-semibold text-[var(--mat-deep)]"
                  : "text-white/80 hover:bg-white/10 hover:text-white"
              }`}
            >
              <span>{item.label}</span>
              <span
                className={`hidden text-[10px] md:inline ${
                  active ? "text-[var(--mat)]/70" : "text-white/40 group-hover:text-white/60"
                }`}
              >
                {item.hint}
              </span>
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto hidden md:block">
        <SidebarStatus />
        <p className="px-5 pb-6 text-[11px] leading-relaxed text-white/45">
          개인 크리에이티브 도구.
          <br />
          모든 생성 이력은 히스토리에 남습니다.
        </p>
      </div>
    </aside>
  );
}
