from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from agromech_api.core.config import get_settings  # noqa: E402
from agromech_api.core.database import get_engine  # noqa: E402
from agromech_api.evaluation.runner import run_evaluation_dataset  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估 AgroMech 检索质量。")
    parser.add_argument("--dataset", default="curated-mvp")
    parser.add_argument("--prompt-version", default="retrieval-v2")
    parser.add_argument("--baseline", type=Path)
    return parser.parse_args()


def assert_acceptance(metrics: dict[str, float], baseline: dict[str, float]) -> None:
    if metrics["protected_identifier_cases"] <= 0:
        raise SystemExit("评测数据集必须包含受保护标识符")
    if metrics["protected_identifier_preservation"] != 1.0:
        raise SystemExit("受保护标识符保留率必须为 1.0")
    if metrics["unauthorized_final_evidence"] != 0:
        raise SystemExit("最终证据不能包含未授权文档")
    if metrics["explicit_model_confusion"] != 0:
        raise SystemExit("显式机型过滤不能产生机型混淆")
    if metrics["recall_at_20"] < baseline["recall_at_20"]:
        raise SystemExit("Recall@20 低于基线")
    if metrics["ndcg_at_10"] < baseline["ndcg_at_10"]:
        raise SystemExit("nDCG@10 低于基线")
    if (
        metrics["recall_at_20"] == baseline["recall_at_20"]
        and metrics["ndcg_at_10"] == baseline["ndcg_at_10"]
    ):
        raise SystemExit("Recall@20 或 nDCG@10 至少有一项必须高于基线")
    if metrics["retrieval_p95_ms"] > baseline["retrieval_p95_ms"] * 1.5:
        raise SystemExit("检索 P95 超过基线的 1.5 倍")


def main() -> int:
    args = parse_args()
    result = run_evaluation_dataset(
        get_engine(),
        settings=get_settings(),
        dataset_version=args.dataset,
        prompt_version=args.prompt_version,
    )
    metrics = result.metrics_summary
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    if args.baseline:
        assert_acceptance(metrics, json.loads(args.baseline.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
