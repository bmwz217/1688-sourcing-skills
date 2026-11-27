"""安全清理选品中已淘汰商品目录，或任务结束后的工作过程目录。"""

from __future__ import annotations

import argparse
import csv
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path

ALLOWED_STATUSES = {"通过1688筛选", "淘汰", "重复淘汰", "定制款暂略"}
REJECTED_STATUSES = {"淘汰", "重复淘汰"}


def is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & mask)


def remove_entry_without_following_links(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    if is_reparse_point(path):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return
    if path.is_dir():
        for child in path.iterdir():
            remove_entry_without_following_links(child)
        path.rmdir()
    elif path.exists():
        path.unlink()


def count_entries_without_following_links(path: Path) -> int:
    if not path.exists():
        return 0
    if is_reparse_point(path):
        return 1
    total = 1
    if path.is_dir():
        for child in path.iterdir():
            total += count_entries_without_following_links(child)
    return total


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def read_manifest(path: Path, prefix: str) -> dict[str, tuple[str, str]]:
    manifest: dict[str, tuple[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"sku", "final_status"}.issubset(reader.fieldnames):
            raise ValueError("状态清单必须包含 sku 和 final_status 两列。")
        for row_number, row in enumerate(reader, start=2):
            sku = (row.get("sku") or "").strip()
            status = (row.get("final_status") or "").strip()
            reason = (row.get("reason") or "").strip()
            if not re.fullmatch(re.escape(prefix) + r"_\d{3,}", sku):
                raise ValueError(f"第 {row_number} 行 SKU 不符合前缀和目录格式：{sku!r}")
            if status not in ALLOWED_STATUSES:
                raise ValueError(f"第 {row_number} 行状态无效：{sku} -> {status!r}")
            if sku in manifest:
                raise ValueError(f"状态清单中 SKU 重复：{sku}")
            manifest[sku] = (status, reason)
    if not manifest:
        raise ValueError("状态清单为空。")
    return manifest


def sku_directories(root: Path, prefix: str) -> dict[str, Path]:
    pattern = re.compile(re.escape(prefix) + r"_\d{3,}")
    result: dict[str, Path] = {}
    for entry in root.iterdir():
        if not pattern.fullmatch(entry.name):
            continue
        if not entry.is_dir():
            raise ValueError(f"SKU 路径存在但不是文件夹：{entry}")
        if is_reparse_point(entry):
            raise ValueError(f"SKU 目录不能是链接或联接点，已停止：{entry}")
        result[entry.name] = entry
    return result


def validate_direct_child(path: Path, root: Path, sku: str) -> None:
    if path.name != sku or path.parent.resolve() != root:
        raise ValueError(f"拒绝操作非选品根目录直接子文件夹：{path}")
    if is_reparse_point(path):
        raise ValueError(f"拒绝操作链接或联接点：{path}")


def write_deletion_log(log_path: Path, root: Path, deleted_rows: list[dict[str, str]]) -> None:
    work = root / "工作过程"
    work_resolved = work.resolve(strict=True)
    log_parent = log_path.parent.resolve()
    if log_parent != work_resolved:
        raise ValueError("清理日志必须保存在工作过程目录内。")
    new_file = not log_path.exists() or log_path.stat().st_size == 0
    with log_path.open("a", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["sku", "final_status", "reason", "result", "deleted_at"])
        if new_file:
            writer.writeheader()
        writer.writerows(deleted_rows)


def clean_rejected(args: argparse.Namespace, root: Path) -> int:
    if not args.sku_prefix or not args.manifest:
        raise ValueError("--delete-rejected 必须同时提供 --sku-prefix 和 --manifest。")
    prefix = args.sku_prefix.strip()
    manifest_path = args.manifest.resolve(strict=True)
    work = root / "工作过程"
    work_resolved = work.resolve(strict=True)
    if not is_within(manifest_path, work_resolved):
        raise ValueError("状态清单必须位于本次选品的工作过程目录内。")

    manifest = read_manifest(manifest_path, prefix)
    directories = sku_directories(root, prefix)
    current_names = set(directories)
    manifest_names = set(manifest)
    unclassified = current_names - manifest_names
    if unclassified:
        raise ValueError("有 SKU 目录未出现在状态清单中：" + ", ".join(sorted(unclassified)))
    missing_retained = [
        sku for sku, (status, _) in manifest.items()
        if status == "通过1688筛选" and sku not in current_names
    ]
    if missing_retained:
        raise ValueError("通过目录缺失，停止删除：" + ", ".join(sorted(missing_retained)))

    reject_names = sorted(
        sku for sku, (status, _) in manifest.items()
        if status in REJECTED_STATUSES and sku in current_names
    )
    already_absent = sorted(
        sku for sku, (status, _) in manifest.items()
        if status in REJECTED_STATUSES and sku not in current_names
    )
    batch_size = args.batch_size
    if batch_size < 1:
        raise ValueError("--batch-size 必须大于 0。")
    batches = (len(reject_names) + batch_size - 1) // batch_size
    print(
        f"状态清单 {len(manifest)} 款；当前 SKU 目录 {len(directories)} 个；"
        f"待清理 {len(reject_names)} 个；此前已不存在 {len(already_absent)} 个；"
        f"保留通过 {len(current_names) - len(reject_names)} 个；批次 {batches}。"
    )
    if not args.execute:
        print("当前为预览模式，没有删除目录。确认清单和数量后再加 --execute。")
        return 0

    log_path = (args.log or (work / "淘汰目录清理日志.csv")).absolute()
    total_deleted = 0
    for batch_index in range(batches):
        batch = reject_names[batch_index * batch_size : (batch_index + 1) * batch_size]
        log_rows = []
        for sku in batch:
            path = directories[sku]
            validate_direct_child(path, root, sku)
            status, reason = manifest[sku]
            remove_entry_without_following_links(path)
            if path.exists():
                raise OSError(f"删除后目录仍存在：{path}")
            log_rows.append({
                "sku": sku,
                "final_status": status,
                "reason": reason,
                "result": "已删除并确认无残留",
                "deleted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            })
            total_deleted += 1
        write_deletion_log(log_path, root, log_rows)
        print(f"已完成批次 {batch_index + 1}/{batches}：累计清理 {total_deleted}/{len(reject_names)} 个目录。")

    remaining = sku_directories(root, prefix)
    expected = {
        sku for sku, (status, _) in manifest.items()
        if status == "通过1688筛选"
    }
    if set(remaining) != expected:
        extra = sorted(set(remaining) - expected)
        missing = sorted(expected - set(remaining))
        raise ValueError(f"清理后目录复核失败；多出：{extra}；缺失：{missing}")
    print(f"清理复核通过：仅保留 {len(remaining)} 个通过目录，无淘汰目录残留。")
    return 0


def clean_work_process(args: argparse.Namespace, root: Path) -> int:
    work = root / "工作过程"
    if not work.exists():
        print("工作过程目录已不存在。")
        return 0
    if is_reparse_point(work) or work.resolve(strict=True).parent != root:
        raise ValueError(f"拒绝清理非根目录直接子文件夹：{work}")
    profit = root / "全店_选品利润表.xlsx"
    if not profit.is_file():
        raise ValueError("利润表不存在，保留工作过程目录以便恢复。")
    count = count_entries_without_following_links(work)
    print(f"工作过程目录包含 {count} 个文件或目录条目；最终利润表存在：{profit.name}。")
    if not args.execute:
        print("当前为预览模式，没有清理。确认最终文件后再加 --execute。")
        return 0
    remove_entry_without_following_links(work)
    if work.exists():
        raise OSError(f"清理后工作过程目录仍存在：{work}")
    print("工作过程目录已清理；目录联接点按链接删除，没有进入其目标。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按已核准的状态清单分批清理淘汰商品目录，或清理已完成任务的工作过程目录。"
    )
    parser.add_argument("--root", type=Path, required=True, help="SKU 文件夹与利润表所在的选品根目录")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--delete-rejected", action="store_true", help="按状态清单清理淘汰和重复淘汰目录")
    mode.add_argument("--delete-work-process", action="store_true", help="在最终文件复核后清理工作过程目录")
    parser.add_argument("--sku-prefix", help="用户提供的 SKU 前缀，例如 ZSD_1116")
    parser.add_argument("--manifest", type=Path, help="位于工作过程目录中的全店最终状态 CSV")
    parser.add_argument("--batch-size", type=int, default=50, help="每批清理数量，默认 50")
    parser.add_argument("--log", type=Path, help="清理日志路径，必须位于工作过程目录")
    parser.add_argument("--execute", action="store_true", help="实际执行；缺省为预览模式")
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir() or is_reparse_point(root):
            raise ValueError("选品根目录无效。")
        if args.delete_rejected:
            return clean_rejected(args, root)
        return clean_work_process(args, root)
    except (OSError, ValueError) as error:
        print(f"清理未完成：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
