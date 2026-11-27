"""按清单下载1688资料，下载阶段结束后再按固定规则筛选。"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit


下载器 = Path(__file__).resolve().parents[2] / "1688-image-downloader-v1.2" / "scripts" / "1688产品图片下载.pyw"
图片格式 = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp"}
结果文件名 = "选品初筛结果.csv"
详细文件名 = "选品初筛详情.json"


def 商品编号(链接):
    if not isinstance(链接, str):
        return None
    地址 = urlsplit(链接)
    匹配 = re.fullmatch(r"/offer/([0-9]+)\.html/?", 地址.path)
    if 地址.scheme in {"http", "https"} and 地址.netloc == "detail.1688.com" and 匹配:
        return 匹配[1]
    return None


def 合法目录名(名称):
    return (
        bool(名称)
        and len(名称) <= 100
        and not re.search(r'[\\/:*?"<>|\x00-\x1f]', 名称)
        and 名称 == 名称.strip(" .")
        and not re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", 名称.split(".")[0])
        and not 名称.startswith("_下载记录")
    )


def 读链接清单(路径, 前缀):
    清单 = {}
    用过的目录 = {}
    for 行号, 原文 in enumerate(路径.read_text(encoding="utf-8-sig").splitlines(), 1):
        行 = 原文.strip()
        if not 行 or 行.startswith("#"):
            continue
        片段 = 行.split("\t")
        if len(片段) not in {1, 2}:
            raise ValueError(f"第{行号}行格式不对。每行写链接，或写 SKU、一个 Tab、链接。")
        编号 = 商品编号(片段[-1].strip())
        if not 编号:
            raise ValueError(f"第{行号}行不是1688商品详情链接。")
        SKU = 片段[0].strip() if len(片段) == 2 else f"{前缀}{编号}"
        if not 合法目录名(SKU):
            raise ValueError(f"第{行号}行的 SKU 不能用作文件夹名称：{SKU}")
        if 编号 in 清单:
            if 清单[编号]["SKU"] != SKU:
                raise ValueError(f"商品 {编号} 对应不同的 SKU，请核对链接清单。")
            continue
        if SKU.casefold() in 用过的目录:
            raise ValueError(f"第{行号}行的 SKU {SKU} 已用于另一个商品。")
        用过的目录[SKU.casefold()] = 编号
        清单[编号] = {
            "SKU": SKU, "商品编号": 编号, "链接": f"https://detail.1688.com/offer/{编号}.html",
        }
    if not 清单:
        raise ValueError("链接清单是空的。")
    return list(清单.values())


def 读商品信息(目录, 编号):
    路径 = 目录 / "商品信息.json"
    if not 路径.is_file():
        return None, "找不到商品信息.json"
    try:
        信息 = json.loads(路径.read_text(encoding="utf-8-sig"), parse_float=Decimal)
        if not isinstance(信息, dict) or 商品编号(信息.get("source_url")) != 编号:
            return None, "商品信息.json 的链接与清单对不上"
        return 信息, ""
    except (OSError, ValueError) as 错误:
        return None, f"读不到商品信息.json：{错误}"


def 数图片(目录):
    return sum(
        p.is_file() and p.suffix.lower() in 图片格式 and p.stat().st_size > 0
        for p in 目录.iterdir()
    ) if 目录.is_dir() else 0


def 价格数字(值):
    if not isinstance(值, (Decimal, int)) or isinstance(值, bool):
        return None
    数字 = Decimal(值)
    return 数字 if 数字.is_finite() and 数字 >= 0 else None


def 整数(值):
    return 值 if type(值) is int and 值 >= 0 else None


def 金额(值):
    return f"{值:.2f}" if 值 is not None else ""


def 已售至少多少(原文):
    if not isinstance(原文, str):
        return None
    文本 = re.sub(r"[\s\u200b\ufeff]+", "", unicodedata.normalize("NFKC", 原文))
    匹配 = re.fullmatch(
        r"已售:?([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)([千万亿])?\+?[个件笔单套条只盒对包袋支双组张卷箱瓶米棵桶盆颗座幅本把根束片枚副扎串粒板提罐筒]?",
        文本,
    )
    if not 匹配:
        return None
    return Decimal(匹配[1].replace(",", "")) * {None: 1, "千": 1000, "万": 10000, "亿": 100000000}[匹配[2]]


def 初筛(信息, 最低价, 最高价):
    淘汰理由, 缺少信息 = [], []
    标题 = 信息.get("title")
    if not 标题:
        缺少信息.append("商品标题")
    已售 = 信息.get("sold_text")
    已售数量 = 已售至少多少(已售)
    if 已售数量 is not None and 已售数量 >= 2000:
        淘汰理由.append(f"卖家显示{已售}，达到2,000")

    起批量 = 整数(信息.get("minimum_order_quantity"))
    if not 起批量:
        缺少信息.append("起批量")
    elif 起批量 > 3:
        淘汰理由.append(f"{起批量}起批，超过3")

    规格 = 信息.get("skus")
    在售, 见过的名称, 有价款 = [], set(), []
    规格不全 = not isinstance(规格, list) or not 规格
    for 款 in 规格 if isinstance(规格, list) else []:
        if not isinstance(款, dict):
            规格不全 = True
            continue
        库存 = 整数(款.get("stock"))
        if 库存 is None:
            规格不全 = True
            continue
        if 库存 == 0:
            continue
        款名 = 款.get("name")
        if not isinstance(款名, str) or not 款名.strip():
            规格不全 = True
            continue
        if 款名.strip() in 见过的名称:
            规格不全 = True
            continue
        见过的名称.add(款名.strip())
        采购价 = 价格数字(款.get("purchase_price_cny"))
        在售.append({"名称": 款名, "采购价": 金额(采购价), "库存": 库存})
        if 采购价 is None:
            规格不全 = True
        else:
            有价款.append(采购价)
            if 采购价 > 最高价:
                淘汰理由.append(f"{款名} ¥{金额(采购价)}，超过最高价 ¥{金额(最高价)}")
    if len(在售) > 12:
        淘汰理由.append(f"已确认有库存的款超过12个（{len(在售)}个）")
    if 规格不全:
        缺少信息.append("有库存的款名、采购价或库存")
    elif not 在售:
        淘汰理由.append("购买区没有有库存的款")
    elif all(价 < 最低价 for 价 in 有价款):
        淘汰理由.append(f"所有在售款都低于 ¥{金额(最低价)}")

    运费信息 = 信息.get("domestic_shipping")
    运费信息 = 运费信息 if isinstance(运费信息, dict) else {}
    return {
        "标题": 标题 or "", "已售": 已售 if 已售数量 is not None else "", "起批量": 起批量 or "",
        "起批单位": 信息.get("minimum_order_unit") or "",
        "在售款数": len(在售), "在售款": 在售,
        "最低在售采购价": 金额(min(有价款)) if 有价款 else "",
        "最高在售采购价": 金额(max(有价款)) if 有价款 else "",
        "国内运费原文": 运费信息.get("text") or "",
        "国内运费金额": 金额(价格数字(运费信息.get("amount_cny"))),
        "运费为起步价": 运费信息.get("is_starting_price"),
        "初筛结果": "淘汰" if 淘汰理由 else "待核数据" if 缺少信息 else "待看图",
        "原因": "、".join(淘汰理由 or 缺少信息) or "核对实际件数、重量和是否带电",
    }


def 下载一件(商品, 根目录):
    目录 = 根目录 / 商品["SKU"]
    信息, 错误 = 读商品信息(目录, 商品["商品编号"])
    if 信息 is not None and 数图片(目录 / "sku") and 数图片(目录 / "产品详情页"):
        print("已有资料，直接用。", flush=True)
        return 目录, "已有资料", ""

    记录目录 = 根目录 / "_下载记录"
    记录目录.mkdir(exist_ok=True)
    暂存目录 = Path(tempfile.mkdtemp(prefix=f'{商品["SKU"]}-', dir=记录目录))
    命令 = [sys.executable, "-u", "-X", "utf8", str(下载器), "--url", 商品["链接"], "--folder", str(暂存目录)]
    成功数 = 失败数 = None
    末行 = ""
    遇到验证 = False
    with (暂存目录 / "下载日志.txt").open("w", encoding="utf-8") as 日志, subprocess.Popen(
        命令, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    ) as 进程:
        for 行 in 进程.stdout:
            print(行, end="", flush=True)
            日志.write(行)
            日志.flush()
            末行 = 行.strip() or 末行
            遇到验证 |= "检测到登录/验证码/真人验证" in 行
            匹配 = re.search(r"图片下载完成：成功 (\d+) 张，失败 (\d+) 张", 行)
            if 匹配:
                成功数, 失败数 = map(int, 匹配.groups())
        退出码 = 进程.wait()

    信息, 读取错误 = 读商品信息(暂存目录, 商品["商品编号"])
    if 信息 is None and 错误 and 目录.exists():
        读取错误 += f"。原目录还在：{错误}"
    if 退出码 and 遇到验证:
        return 暂存目录, "验证中断", "登录或验证码未完成"
    if 退出码:
        return 暂存目录, "下载失败", 末行 or f"下载器退出码 {退出码}"
    if 信息 is None or not 成功数 or 失败数 != 0:
        return 暂存目录, "下载不全", 读取错误 or f"图片成功 {成功数} 张，失败 {失败数} 张"
    if not 目录.exists():
        暂存目录.rename(目录)
        return 目录, "下载完成", ""
    return 暂存目录, "下载完成", "原文件夹已存在，本次资料存到下载记录目录"


def 写结果(目录, 记录):
    列名 = [
        "SKU", "商品编号", "链接", "资料文件夹", "下载结果", "下载说明", "SKU图数", "详情图数",
        "标题", "已售", "起批量", "起批单位", "在售款数", "最低在售采购价", "最高在售采购价",
        "国内运费原文", "国内运费金额", "运费为起步价", "初筛结果", "原因",
    ]
    临时 = 目录 / f".{结果文件名}.tmp"
    with 临时.open("w", encoding="utf-8-sig", newline="") as 文件:
        写入 = csv.DictWriter(文件, fieldnames=列名, extrasaction="ignore")
        写入.writeheader()
        写入.writerows(记录)
    临时.replace(目录 / 结果文件名)
    临时 = 目录 / f".{详细文件名}.tmp"
    临时.write_text(json.dumps(记录, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    临时.replace(目录 / 详细文件名)


def 执行(参数):
    根目录 = 参数.folder.resolve()
    清单 = 读链接清单(参数.links, 参数.prefix)
    if not 参数.local_only and not 下载器.is_file():
        raise ValueError(f"找不到1688下载器：{下载器}")
    根目录.mkdir(parents=True, exist_ok=True)
    结果目录 = {条["SKU"]: (根目录 / 条["SKU"], "只读本地资料", "") for 条 in 清单}
    if not 参数.local_only:
        # 单件下载结束就保存路径，于中断后复用已完成的下载。
        进度文件 = 根目录 / "下载进度.json"
        进度 = json.loads(进度文件.read_text(encoding="utf-8")) if 进度文件.is_file() else {}
        for i, 条 in enumerate(清单, 1):
            sku = 条["SKU"]
            print(f'[{i}/{len(清单)}] {sku}', flush=True)
            旧 = 进度.get(sku)
            if 旧 and 旧["商品编号"] == 条["商品编号"] and 旧["状态"] in {"下载完成", "已有资料"}:
                旧目录 = Path(旧["路径"]).resolve()
                if 旧目录.is_relative_to(根目录) and 读商品信息(旧目录, 条["商品编号"])[0] is not None and 数图片(旧目录 / "sku") and 数图片(旧目录 / "产品详情页"):
                    结果目录[sku] = 旧目录, "已有资料", ""
                    continue
            结果目录[sku] = 下载一件(条, 根目录)
            路径, 状态, 说明 = 结果目录[sku]
            进度[sku] = {"商品编号": 条["商品编号"], "路径": str(路径), "状态": 状态, "说明": 说明}
            临时 = 根目录 / ".下载进度.json.tmp"
            临时.write_text(json.dumps(进度, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            临时.replace(进度文件)
            if 状态 == "验证中断":
                raise ValueError("1688验证中断，已保存下载进度。完成验证后重新运行。")

    记录 = []
    for 条 in 清单:
        路径, 状态, 说明 = 结果目录[条["SKU"]]
        信息, 错误 = 读商品信息(路径, 条["商品编号"])
        判定 = 初筛(信息, 参数.min_price, 参数.max_price) if 信息 is not None else {
            "初筛结果": "待核数据", "原因": 错误,
        }
        原图数 = 数图片(路径 / "sku")
        详情图数 = 数图片(路径 / "产品详情页")
        if 判定["初筛结果"] == "待看图" and (not 原图数 or not 详情图数 or 状态 == "下载不全"):
            判定.update({"初筛结果": "待核数据", "原因": "原图未下载齐，先补图"})
        记录.append({
            **条, "资料文件夹": str(路径), "下载结果": 状态, "下载说明": 说明,
            "SKU图数": 原图数, "详情图数": 详情图数, **判定,
        })
    写结果(根目录, 记录)
    for 状态 in ("淘汰", "待核数据", "待看图"):
        print(f'{状态} {sum(条["初筛结果"] == 状态 for 条 in 记录)} 件')
    print(f"结果：{根目录 / 结果文件名}")


def main():
    解析器 = argparse.ArgumentParser(description="读商品链接清单，整批下载，下载后再按本地购买数据初筛。")
    解析器.add_argument("--links", type=Path, required=True, help="UTF-8 文本，每行写一条1688链接")
    解析器.add_argument("--folder", type=Path, required=True, help="整批商品资料的总文件夹")
    解析器.add_argument("--prefix", required=True, help="自动生成的 SKU 前缀，后面接1688商品编号")
    解析器.add_argument("--min-price", type=Decimal, default=Decimal("17"))
    解析器.add_argument("--max-price", type=Decimal, default=Decimal("65"))
    解析器.add_argument("--local-only", action="store_true", help="只读已经下载到 SKU 文件夹的资料")
    参数 = 解析器.parse_args()
    if not (参数.min_price.is_finite() and 参数.max_price.is_finite() and 0 <= 参数.min_price <= 参数.max_price):
        解析器.error("采购价范围无效。")
    try:
        执行(参数)
        return 0
    except (OSError, ValueError, InvalidOperation) as 错误:
        print(f"暂停：{错误}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
