"""piemgmaker CLI — run | manifest | golden | bench | serve."""

import argparse
import json
import sys
import uuid
from pathlib import Path

import yaml
from dotenv import find_dotenv, load_dotenv

from piemgmaker import __version__
from piemgmaker.assets_lib.library import AssetLibrary
from piemgmaker.bench import load_bench_set, validate_set
from piemgmaker.config import Config, load_config
from piemgmaker.engine import create_engine
from piemgmaker.golden.runner import run_cases
from piemgmaker.pipeline.orchestrate import build_job, execute_job
from piemgmaker.schemas.brief import BriefInput
from piemgmaker.schemas.manifest import build_manifest
from piemgmaker.schemas.model_profile import ModelProfileRegistry, load_model_profiles
from piemgmaker.schemas.style_pack import load_style_packs
from piemgmaker.workflows.render import load_template

WORKFLOW_IDS = ("object-gen-v1", "object-inpaint-v1")


def _load_env() -> None:
    """CWD에서 위로 올라가며 .env를 찾아 환경에 주입한다.

    override=False — 실제 환경변수가 .env를 이긴다(일회성 우회를 앞에 붙여 쓸 수 있게).
    로드는 이 진입점에만 둔다 — load_config()에서 읽으면 테스트가 리포의 실제 .env를 흡수한다.
    """
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)


def _print_engine(config: Config) -> None:
    """어느 백엔드로 실행되는지 stderr에 남긴다 — 토큰은 출력하지 않는다.

    stdout은 run 서브커맨드의 JSON 계약이라 쓰지 않는다. 설정이 어긋난 채로 생성이 조용히
    로컬 ComfyUI로 나가는 것을 막는 것이 목적이다.
    """
    if config.engine_flavor == "remote":
        targets = "  ".join(f"{group}={url}" for group, url in sorted(config.remote_urls.items()))
        print(f"engine={config.engine}  {targets}", file=sys.stderr)
        return
    print(f"engine={config.engine}  backend={config.backend_url}", file=sys.stderr)
    if config.remote_urls:
        groups = ", ".join(sorted(config.remote_urls))
        print(
            f"경고: 원격 URL이 설정돼 있지만({groups}) PM_ENGINE={config.engine}입니다 "
            "— 로컬 백엔드로 실행됩니다.",
            file=sys.stderr,
        )


def _load_profiles(path: Path) -> ModelProfileRegistry | None:
    return load_model_profiles(path) if path.is_file() else None


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    _print_engine(config)
    packs = load_style_packs(args.packs_dir)
    brief = BriefInput.model_validate(yaml.safe_load(args.brief.read_text(encoding="utf-8")))
    library = AssetLibrary(args.assets_dir) if (brief.assets or brief.logo_reference) else None
    job_id = uuid.uuid4().hex[:12]
    build = build_job(
        brief,
        packs,
        library=library,
        profiles=_load_profiles(args.profiles),
        job_id=job_id,
        workdir=config.storage / "jobs" / job_id / "inputs",
        skip_matting=args.skip_matting,
        engine_flavor=config.engine_flavor,
    )
    engine = create_engine(config)
    result = execute_job(build, engine, config, timeout_s=args.timeout)
    print(
        json.dumps(
            {
                "job_id": result.job_id,
                "job_dir": str(result.job_dir),
                "strategy": result.strategy,
                "passed": result.report.passed,
                "candidates": [
                    {
                        "index": c.index,
                        "seed": c.seed,
                        "final": str(c.final_path) if c.final_path else None,
                        "passed": c.qa.passed,
                    }
                    for c in result.candidates
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.report.passed else 1


def cmd_manifest(args: argparse.Namespace) -> int:
    config = load_config()
    packs = load_style_packs(args.packs_dir)
    workflows = [load_template(workflow_id)[1] for workflow_id in WORKFLOW_IDS]
    report = build_manifest(
        config,
        engine_name=config.engine,
        package_version=__version__,
        workflows=workflows,
        style_packs=packs,
    )
    print(report.model_dump_json(indent=2))
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    results = run_cases(args.cases, args.produced)
    if not results:
        print(f"골든 케이스가 없습니다: {args.cases}", file=sys.stderr)
        return 2
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        detail = f"ssim={r.ssim} iou={r.iou} mae={r.mae}"
        if r.failures:
            detail += " | " + "; ".join(r.failures)
        print(f"[{mark}] {r.id}: {detail}")
    return 0 if all(r.passed for r in results) else 1


def cmd_bench(args: argparse.Namespace) -> int:
    from piemgmaker.bench import run_bench
    from piemgmaker.matting_runners import default_runners

    bench_set, base_dir = load_bench_set(args.set)
    problems = validate_set(bench_set, base_dir)
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"세트 유효 — 항목 {len(bench_set.items)}개")
        return 0
    config = load_config()
    rows = run_bench(bench_set, base_dir, default_runners(config), args.out)
    print(f"{len(rows)}행 측정 → {args.out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from piemgmaker.server.app import create_app
    from piemgmaker.server.jobs import JobStore

    config = load_config()
    _print_engine(config)
    packs = load_style_packs(args.packs_dir)
    store = JobStore(
        config,
        packs,
        args.assets_dir,
        engine_factory=lambda: create_engine(config),
        timeout_s=args.timeout,
        profiles=_load_profiles(args.profiles),
    )
    store.start_worker()
    app = create_app(store, config, args.assets_dir, samples_dir=args.samples_dir)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_env()
    parser = argparse.ArgumentParser(prog="piemgmaker", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="브리프 실행 → 알파 PNG k장 + qa_report.json")
    run_p.add_argument("--brief", required=True, type=Path)
    run_p.add_argument("--packs-dir", type=Path, default=Path("style_packs"))
    run_p.add_argument("--assets-dir", type=Path, default=Path("assets"))
    run_p.add_argument("--profiles", type=Path, default=Path("model_profiles.yaml"))
    run_p.add_argument("--timeout", type=float, default=600.0)
    run_p.add_argument(
        "--skip-matting",
        action="store_true",
        help="플러밍 검증용 — 매팅 조각 없이 실행 (알파 미보장)",
    )
    run_p.set_defaults(func=cmd_run)

    manifest_p = sub.add_parser("manifest", help="자가 보고 JSON 출력 (서버는 /api/manifest로 동일 규격 제공)")
    manifest_p.add_argument("--packs-dir", type=Path, default=Path("style_packs"))
    manifest_p.set_defaults(func=cmd_manifest)

    golden_p = sub.add_parser("golden", help="골든 세트 러너 (SSIM + 알파 IoU + soft alpha MAE)")
    golden_p.add_argument("--cases", type=Path, default=Path("golden/cases"))
    golden_p.add_argument("--produced", required=True, type=Path, help="산출물 디렉토리 (<case-id>.png)")
    golden_p.set_defaults(func=cmd_golden)

    bench_p = sub.add_parser(
        "bench",
        help="배경 제거 비교 하니스 — 추출형 2러너(segment·trimap) 비교. native_alpha는 전용 워크플로우로 별도 평가.",
    )
    bench_p.add_argument("--set", type=Path, default=Path("bench/matting_set/set.yaml"))
    bench_p.add_argument("--out", type=Path, default=Path("out/matting-comparison.md"))
    bench_p.add_argument("--dry-run", action="store_true")
    bench_p.set_defaults(func=cmd_bench)

    serve_p = sub.add_parser("serve", help="웹 API 서버 (FastAPI) — Next.js 프론트가 사용")
    serve_p.add_argument(
        "--host",
        default="127.0.0.1",
        help="127.0.0.1 유지 권장 — 다기기 접속은 Next.js만 -H 0.0.0.0으로 연다(Next가 서버사이드 프록시). "
        "이 서버는 무인증이므로 직접 노출은 신뢰된 네트워크에서만.",
    )
    serve_p.add_argument("--port", type=int, default=8787)
    serve_p.add_argument("--packs-dir", type=Path, default=Path("style_packs"))
    serve_p.add_argument("--assets-dir", type=Path, default=Path("assets"))
    serve_p.add_argument("--profiles", type=Path, default=Path("model_profiles.yaml"))
    serve_p.add_argument("--samples-dir", type=Path, default=Path("samples"))
    serve_p.add_argument("--timeout", type=float, default=14400.0)  # 1024²×8장 최악 케이스 여유
    serve_p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
