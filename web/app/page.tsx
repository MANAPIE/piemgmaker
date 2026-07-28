import BriefForm from "@/components/BriefForm";
import QueuePanel from "@/components/QueuePanel";
import SampleGallery from "@/components/SampleGallery";

export default async function CreatePage({
  searchParams,
}: {
  searchParams: Promise<{ from?: string }>;
}) {
  const { from } = await searchParams;
  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight">생성하기</h1>
        <p className="mt-1 text-sm text-[var(--ink-soft)]">
          브리프를 넣으면 배너에 올릴 오브젝트를 <b>알파 배경 PNG</b>로 만들어 드립니다.
          {from && " 이전 잡의 입력을 불러왔습니다 — 수정 후 다시 생성하세요."}
        </p>
      </header>
      <QueuePanel />
      <BriefForm fromJobId={from} />
      <SampleGallery />
    </div>
  );
}
