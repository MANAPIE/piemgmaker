"use client";

// 배경 프리뷰 토글 — translucent 검수용: 체커보드/밝음/어두움/브랜드 컬러/사진 1종
// 매트 위 색 견본(swatch)처럼 배경 자체를 버튼에 보여준다.

export type PreviewBg = "checker" | "light" | "dark" | "brand" | "photo";

const BRAND_COLOR = process.env.NEXT_PUBLIC_BRAND_COLOR ?? "#0F62FE";

export const BG_OPTIONS: { key: PreviewBg; label: string }[] = [
  { key: "checker", label: "체커보드" },
  { key: "light", label: "밝음" },
  { key: "dark", label: "어두움" },
  { key: "brand", label: "브랜드" },
  { key: "photo", label: "사진" },
];

export function previewStyle(bg: PreviewBg): React.CSSProperties {
  switch (bg) {
    case "checker":
      return {
        background:
          "repeating-conic-gradient(var(--checker-a) 0% 25%, var(--checker-b) 0% 50%) 50% / 20px 20px",
      };
    case "light":
      return { background: "#ffffff" };
    case "dark":
      return { background: "#17181a" };
    case "brand":
      return { background: BRAND_COLOR };
    case "photo":
      return { background: "url(/preview-bg.png) center / cover" };
  }
}

export default function BackgroundToggle({
  value,
  onChange,
}: {
  value: PreviewBg;
  onChange: (bg: PreviewBg) => void;
}) {
  return (
    <div role="radiogroup" aria-label="배경 프리뷰" className="flex items-center gap-1.5">
      {BG_OPTIONS.map((option) => (
        <button
          key={option.key}
          type="button"
          role="radio"
          aria-checked={value === option.key}
          onClick={() => onChange(option.key)}
          title={option.label}
          className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-xs transition-colors ${
            value === option.key
              ? "border-[var(--mat)] bg-[var(--mat-tint)] font-semibold"
              : "border-[var(--line)] bg-white hover:border-[var(--mat)]/50"
          }`}
        >
          <span
            aria-hidden
            className="block h-4 w-4 rounded-[3px] ring-1 ring-black/10"
            style={previewStyle(option.key)}
          />
          {option.label}
        </button>
      ))}
    </div>
  );
}
