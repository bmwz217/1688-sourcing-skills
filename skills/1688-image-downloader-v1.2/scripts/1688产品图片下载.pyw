"""通过 CDP 连接已登录浏览器，下载 1688 商品图、详情文本和购买区信息。

不带参数运行时打开桌面界面；传入“1688 链接”和“保存文件夹”时自动下载。
"""

import argparse
import html
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from tempfile import TemporaryDirectory
import ctypes
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests
from PIL import Image

# 本程序的所有网络访问都禁止读取系统/环境代理。这里只修改当前进程及其
# 启动的子进程，不会修改 Windows 的全局代理设置。
代理环境变量 = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)
for 变量 in 代理环境变量:
    os.environ.pop(变量, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"


def 创建直连会话() -> requests.Session:
    """创建不读取环境代理、明确使用直连的 requests 会话。"""
    会话 = requests.Session()
    会话.trust_env = False
    会话.proxies = {"http": None, "https": None}
    return 会话


# 屏蔽 Playwright 内置 Node 运行时的无关弃用警告。
os.environ.setdefault("NODE_NO_WARNINGS", "1")
from playwright.sync_api import Response, sync_playwright


# 首次使用时通常只需修改这里；程序运行时不需要命令行参数。
CDP地址 = "http://127.0.0.1:9222"
浏览器路径 = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
# 新版 Chrome 禁止在系统默认用户目录上开启远程调试，因此使用项目内的专用目录。
# 第一次运行时请在弹出的浏览器里登录 1688；以后登录状态会保存在此目录中。
浏览器用户目录环境变量 = os.environ.get("CODEX_SKILL_1688_USER_DATA", "").strip()
浏览器用户目录 = (
    Path(浏览器用户目录环境变量).expanduser().resolve()
    if 浏览器用户目录环境变量
    else Path(__file__).resolve().parent / "浏览器用户数据"
)
浏览器配置名称 = "Default"
下载根目录 = Path(__file__).resolve().parent / "下载结果"
下载线程数 = 8
下载重试次数 = 3
下载重试间隔 = 1.5
测试链接 = "https://detail.1688.com/offer/980897332344.html"
接口关键字 = "h5api.m.1688.com/h5/mtop.1688.wosc.queryofferskuselectormode"
详情接口关键字 = "h5api.m.1688.com/h5/mtop.1688.mmga.offerdetail.service"


def 清理文件名(名称: str, 默认名称: str = "未命名") -> str:
    名称 = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (名称 or "").strip())
    名称 = re.sub(r"\s+", " ", 名称).rstrip(". ")
    return (名称 or 默认名称)[:150]


def 启动浏览器() -> subprocess.Popen | None:
    """若 CDP 尚未开启，则用本地用户目录启动浏览器。"""
    try:
        with 创建直连会话() as 会话:
            会话.get(f"{CDP地址}/json/version", timeout=2).raise_for_status()
        print("已发现开启 CDP 的浏览器。请确认它是本程序启动的无代理浏览器；否则请先关闭后重试。")
        return None
    except requests.RequestException:
        pass

    if not 浏览器路径.exists():
        raise FileNotFoundError(f"找不到浏览器：{浏览器路径}\n请修改脚本顶部的“浏览器路径”。")

    浏览器用户目录.mkdir(parents=True, exist_ok=True)
    print(f"未发现 CDP 浏览器，正在启动专用 Chrome……\n用户数据目录：{浏览器用户目录}")
    命令 = [
        str(浏览器路径),
        "--remote-debugging-port=9222",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--user-data-dir={浏览器用户目录}",
        f"--profile-directory={浏览器配置名称}",
        "--no-first-run",
        "--no-default-browser-check",
        # 同时使用 Chromium 的直连参数，避免读取 Windows/浏览器代理设置。
        "--no-proxy-server",
        "--proxy-server=direct://",
        "--proxy-bypass-list=*",
    ]
    进程 = subprocess.Popen(命令)
    for _ in range(20):
        if 进程.poll() is not None:
            raise RuntimeError(f"Chrome 启动后立即退出，退出代码：{进程.returncode}")
        try:
            with 创建直连会话() as 会话:
                会话.get(f"{CDP地址}/json/version", timeout=1).raise_for_status()
            print("浏览器 CDP 已就绪。")
            return 进程
        except requests.RequestException:
            time.sleep(1)
    raise RuntimeError("Chrome 已打开，但 CDP 端口在 20 秒内没有就绪。请关闭刚打开的专用 Chrome 后重试。")


def 解析接口数据(原始文本: str) -> dict:
    """兼容标准 JSON 与 mtop(...)/JSONP 格式。"""
    文本 = 原始文本.strip()
    try:
        数据 = json.loads(文本)
    except json.JSONDecodeError:
        开始, 结束 = 文本.find("("), 文本.rfind(")")
        if 开始 < 0 or 结束 <= 开始:
            raise
        数据 = json.loads(文本[开始 + 1:结束])

    # 某些接口会把 data 再编码成 JSON 字符串。
    if isinstance(数据.get("data"), str):
        try:
            数据["data"] = json.loads(数据["data"])
        except json.JSONDecodeError:
            pass
    return 数据


def 查找业务模型(数据) -> dict:
    """递归查找包含 SKU 属性和列表的模型，兼容多种页面结构。"""
    if isinstance(数据, list):
        for 项 in 数据:
            结果 = 查找业务模型(项)
            if 结果:
                return 结果
        return {}
    if not isinstance(数据, dict):
        return {}
    if isinstance(数据.get("skuProps"), list) and any(
        键 in 数据 for 键 in ("skuInfoMap", "originalSkuInfoMap", "skuInfoMapOriginal", "skuMap", "skuModel")
    ):
        return 数据
    模型 = 数据.get("skuSelectorBizModel")
    if isinstance(模型, dict):
        return 模型
    for 值 in 数据.values():
        if isinstance(值, dict):
            结果 = 查找业务模型(值)
            if 结果:
                return 结果
    return {}


def 提取SKU(数据: dict) -> list[dict]:
    模型 = 查找业务模型(数据)
    if not 模型:
        return []

    图片映射 = {}
    for 属性组 in 模型.get("skuProps", []):
        for 属性值 in 属性组.get("value", []):
            名称 = str(属性值.get("name") or "").strip()
            图片 = 属性值.get("imageUrl") or 属性值.get("image") or ""
            if 名称 and 图片:
                图片映射[名称] = 图片

    结果 = []
    # 1688 新版接口使用 skuInfoMap，旧版接口使用 skuModel.skuMap。
    sku列表 = 模型.get("skuInfoMap") or 模型.get("originalSkuInfoMap") or 模型.get("skuInfoMapOriginal")
    if not sku列表:
        sku列表 = 模型.get("skuMap") or 模型.get("skuModel", {}).get("skuMap", [])
    if isinstance(sku列表, dict):
        # 新版字典的键通常就是完整规格名；个别记录内部可能不再包含 specAttrs。
        sku列表 = [dict(值, specAttrs=值.get("specAttrs") or 键) for 键, 值 in sku列表.items()]
    for 序号, sku in enumerate(sku列表, 1):
        # 部分商品使用 &gt; 等 HTML 实体分隔规格，须先解码再匹配图片。
        名称 = html.unescape(str(sku.get("specAttrs") or sku.get("skuName") or f"SKU_{序号}"))
        图片 = sku.get("imageUrl") or ""
        if not 图片:
            for 片段 in re.split(r"[>;；]", 名称):
                片段 = 片段.strip()
                if 图片映射.get(片段):
                    图片 = 图片映射[片段]
                    break
        if 图片:
            结果.append({"名称": 名称, "图片": 补全链接(图片)})
    return 结果


def 从页面内嵌数据提取SKU(页面) -> list[dict]:
    """从页面脚本中的 skuModel/skuSelectorBizModel JSON 提取 SKU。"""
    源码 = 页面.content()
    解码器 = json.JSONDecoder()
    for 字段 in ("skuSelectorBizModel", "skuModel"):
        for 匹配 in re.finditer(rf'["\']{字段}["\']\s*:\s*', 源码):
            开始 = 匹配.end()
            try:
                模型, _ = 解码器.raw_decode(源码[开始:])
                结果 = 提取SKU(模型)
                if 结果:
                    return 结果
            except (json.JSONDecodeError, TypeError):
                continue
    return []


def 从页面按钮提取SKU(页面) -> list[dict]:
    """接口和内嵌数据均不可用时，从可见规格按钮提取图片。"""
    结果 = []
    按钮 = 页面.locator("#skuSelection button.sku-filter-button")
    for 序号 in range(按钮.count()):
        当前 = 按钮.nth(序号)
        图片 = 当前.locator("img").first
        if not 图片.count():
            continue
        名称节点 = 当前.locator(".label-name").first
        名称 = 名称节点.inner_text().strip() if 名称节点.count() else f"SKU_{序号 + 1}"
        链接 = 图片.get_attribute("src") or 图片.get_attribute("data-src") or ""
        if not 链接:
            continue
        链接 = 补全链接(链接)
        # DOM 通常显示缩略图，去掉阿里图片地址末尾的缩略图样式以获取原图。
        链接 = re.sub(r"_(?:sum|\d+x\d+)\.jpg(?=$|\?)", "", 链接, flags=re.I)
        结果.append({"名称": 名称, "图片": 链接})
    return 结果


def 补全链接(链接: str) -> str:
    if 链接.startswith("//"):
        return "https:" + 链接
    return 链接


def 检查真人验证(页面, 等待验证=None) -> None:
    """发现登录或验证码迹象时，暂停等待用户处理。"""
    验证选择器 = [
        "iframe[src*='login']", "iframe[src*='captcha']", "#nocaptcha",
        ".nc-container", "[class*='captcha']", "[class*='verify']",
    ]
    地址 = 页面.url.lower()
    需处理 = any(词 in 地址 for 词 in ("login", "captcha", "verify"))
    if not 需处理:
        for 选择器 in 验证选择器:
            try:
                if 页面.locator(选择器).first.is_visible(timeout=300):
                    需处理 = True
                    break
            except Exception:
                continue
    if 需处理:
        print("\n检测到登录/验证码/真人验证，请在浏览器中完成操作。")
        if 等待验证:
            等待验证()
        else:
            input("完成后回到此窗口，按回车继续……")
        页面.wait_for_timeout(1500)


def 滚动加载详情图(页面, 等待验证=None) -> list[str]:
    """逐屏滚动以触发详情区域懒加载，然后收集图片地址。"""
    上次高度 = 0
    for _ in range(40):
        检查真人验证(页面, 等待验证)
        页面.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.8, 600))")
        页面.wait_for_timeout(350)
        高度 = 页面.evaluate("document.documentElement.scrollHeight")
        到底 = 页面.evaluate("window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 50")
        if 到底 and 高度 == 上次高度:
            break
        上次高度 = 高度

    # 保留旧版页面选择器，并兼容详情图直接位于 #detail 内的页面布局。
    详情图选择器 = [
        'div[id="detail"] > div img',
        "div#description div#detail img",
    ]
    图片列表 = []
    for 选择器 in 详情图选择器:
        图片列表.extend(
            页面.locator(选择器).evaluate_all(
                """imgs => imgs.map(img =>
                    img.currentSrc || img.src || img.dataset.src || img.dataset.lazyload ||
                    img.getAttribute('data-src') || img.getAttribute('data-lazyload') || ''
                ).filter(Boolean)"""
            )
        )
    return list(dict.fromkeys(补全链接(链接) for 链接 in 图片列表 if 链接 and not 链接.startswith("data:")))


def 等待商品详情内容(页面, 超时毫秒: int = 15000) -> None:
    """等待 #description 内的开放 Shadow DOM 注入商品详情文本。"""
    try:
        页面.wait_for_function(
            """() => {
                const root = document.querySelector('#description');
                if (!root) return false;

                function hasShadowText(node) {
                    if (node.shadowRoot) {
                        if ((node.shadowRoot.textContent || '').trim()) return true;
                        if (hasShadowText(node.shadowRoot)) return true;
                    }
                    return Array.from(node.children || []).some(hasShadowText);
                }

                return hasShadowText(root);
            }""",
            timeout=超时毫秒,
        )
    except Exception:
        # 有些商品详情只有图片，没有网页文本；这种情况继续保存已获取到的文本。
        print("等待 #description 商品详情文本超时，继续保存当前已获取到的网页文本。")


def 提取详情文本(页面) -> str:
    """按页面 ID 提取包装信息和商品详情节点中的全部文本并组合。"""
    # 1688 页面版本会改变详情区域内部的 HTML 结构，但这两个 ID 是文本内容的稳定入口。
    # 将普通 DOM 与开放 Shadow DOM 临时还原到一个离屏副本，再读取 innerText。
    # 这样既能读到 v-detail-p 等自定义元素里的详情，也能保留表格单元格的换行/制表符。
    文本片段 = []
    for 选择器 in ("#productPackInfo", "#description"):
        文本片段.extend(
            页面.locator(选择器).evaluate_all(
                """elements => {
                    const ignoredTags = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
                    const standardTags = new Set([
                        'A', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'BR', 'BUTTON', 'CODE', 'DD',
                        'DIV', 'DL', 'DT', 'EM', 'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FOOTER',
                        'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HR', 'I',
                        'LABEL', 'LI', 'MAIN', 'NAV', 'OL', 'P', 'PRE', 'SECTION', 'SMALL',
                        'SPAN', 'STRONG', 'TABLE', 'TBODY', 'TD', 'TFOOT', 'TH', 'THEAD',
                        'TR', 'UL', 'IFRAME', 'FRAME'
                    ]);

                    function cloneWithShadow(node) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            return document.createTextNode(node.nodeValue || '');
                        }
                        if (
                            node.nodeType !== Node.ELEMENT_NODE &&
                            node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE
                        ) {
                            return null;
                        }
                        if (
                            node.nodeType === Node.ELEMENT_NODE &&
                            ignoredTags.has(node.tagName)
                        ) {
                            return null;
                        }

                        const tagName = node.nodeType === Node.ELEMENT_NODE && standardTags.has(node.tagName)
                            ? node.tagName.toLowerCase()
                            : 'div';
                        const clone = document.createElement(tagName);
                        const children = [];

                        // 自定义元素（例如 v-detail-p）的正文可能位于开放 Shadow DOM。
                        if (node.shadowRoot) {
                            children.push(...node.shadowRoot.childNodes);
                        }
                        if (
                            node.nodeType === Node.ELEMENT_NODE &&
                            (node.tagName === 'IFRAME' || node.tagName === 'FRAME') &&
                            node.contentDocument?.body
                        ) {
                            children.push(...node.contentDocument.body.childNodes);
                        }
                        children.push(...node.childNodes);
                        for (const child of children) {
                            const childClone = cloneWithShadow(child);
                            if (childClone) clone.appendChild(childClone);
                        }
                        return clone;
                    }

                    function collectText(element) {
                        const holder = document.createElement('div');
                        holder.style.cssText = 'position:fixed;left:-100000px;top:-100000px;width:1200px;';
                        const clone = cloneWithShadow(element);
                        if (!clone) return '';
                        holder.appendChild(clone);
                        document.body.appendChild(holder);
                        const cloneText = holder.innerText || holder.textContent || '';
                        holder.remove();
                        // 闭合 Shadow DOM 无法从 shadowRoot 读取，但浏览器实际渲染文本
                        // 仍可能出现在宿主元素的 innerText 中，二者取信息更完整的一份。
                        const renderedText = element.innerText || element.textContent || '';
                        return cloneText.length >= renderedText.length ? cloneText : renderedText;
                    }

                    return elements.map(collectText).filter(text => text.trim());
                }"""
            )
        )
    原始文本 = "\n".join(文本片段)
    文本行 = []
    for 行 in 原始文本.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        # 将不换行空格、制表符等压缩为普通空格，同时保留详情内容的逐行结构。
        整理后 = re.sub(r"[\t\f\v\u00a0 ]+", " ", 行).strip()
        if 整理后:
            文本行.append(整理后)
    结果 = "\n".join(文本行)

    # 平台说明不属于商品详情；从“平台活动下价格”标记开始全部截断。
    # 同时清理标记前可能残留的中文括号或 Markdown 加粗符号。
    截断位置 = 结果.find("平台活动下价格")
    if 截断位置 >= 0:
        结果 = re.sub(r"[【\[*\s]+$", "", 结果[:截断位置])
    return 结果


def 提取购买区信息(页面, 链接: str) -> dict:
    """读取购买区的可见信息。页面没有给出明确值时，保留原文并将数值留空。"""
    购买区 = 页面.evaluate(r"""() => {
        const 取文本 = node => node?.innerText.trim() || null;
        const 查找 = selector => [...document.querySelectorAll(`#cartScrollBar ${selector}`)];
        return {
            title: 取文本(查找('h1')[0]),
            sales: 查找('.slick-list').map(取文本).filter(Boolean),
            shipping: 查找('.service-item.split-border').map(取文本).filter(Boolean),
            orderMinimum: 查找('.module-od-main-price').map(取文本).filter(Boolean),
            skus: 查找('.feature-item .expand-view-list > .expand-view-item').map(row => ({
                name: 取文本(row.querySelector('.item-label')),
                fields: [...row.querySelectorAll('.item-price-stock')].map(取文本).filter(Boolean),
            })),
        };
    }""")
    成交文本 = {
        文本.strip()
        for 区块 in 购买区["sales"]
        for 行 in 区块.splitlines()
        for 文本 in re.findall(r"已售\s*\d[\d,]*(?:\.\d+)?\s*(?:万|亿)?\s*\+?\s*(?:个|件|笔|单|套|条|只|支)?", 行)
    }
    已售 = next(iter(成交文本)) if len(成交文本) == 1 else None

    运费原文 = set(文本 for 文本 in 购买区["shipping"] if re.search(r"运费|包邮", 文本))
    运费 = next(iter(运费原文)) if len(运费原文) == 1 else None
    运费匹配 = re.fullmatch(r"运费\s*[:：]?\s*[¥￥]\s*([0-9]+(?:\.[0-9]+)?)\s*(起)?", 运费 or "")
    运费金额 = float(运费匹配[1]) if 运费匹配 else None
    运费是起价 = bool(运费匹配[2]) if 运费匹配 else None
    if 运费 in {"包邮", "免运费", "运费：包邮", "运费:包邮"}:
        运费金额, 运费是起价 = 0.0, False

    # 只查主价格区，商品包材和贴标服务有各自的起订量。
    起订单位 = r"(?:个|件|套|条|只|盒|对|包|袋|支|双|组|张|卷|箱|瓶|米)"
    起订数量 = r"[1-9][0-9]*(?:,[0-9]{3})*"
    起批样式 = (
        rf"(?<![0-9.,])({起订数量})\s*({起订单位})\s*起(?:批|订|购)(?!量)",
        rf"起(?:批|订|购)量[ \t]*[:：]?[ \t]*({起订数量})[ \t]*({起订单位})?(?![0-9.,])",
    )
    起批证据 = {}
    for 区块 in 购买区["orderMinimum"]:
        for 样式 in 起批样式:
            for 匹配 in re.finditer(样式, 区块):
                数量 = int(匹配[1].replace(",", ""))
                起批证据.setdefault((数量, 匹配[2]), 匹配[0].strip())
    起批量, 起批单位, 起批原文 = None, None, None
    if len(起批证据) == 1:
        (起批量, 起批单位), 起批原文 = next(iter(起批证据.items()))

    规格 = []
    for 行 in 购买区["skus"]:
        匹配价格 = [文本 for 文本 in 行["fields"] if 文本.startswith(("¥", "￥"))]
        匹配库存 = [文本 for 文本 in 行["fields"] if 文本.startswith("库存")]
        价格原文 = 匹配价格[0] if len(匹配价格) == 1 else None
        库存原文 = 匹配库存[0] if len(匹配库存) == 1 else None
        价格 = re.fullmatch(r"[¥￥]\s*([0-9]+(?:\.[0-9]+)?)", 价格原文 or "")
        库存 = re.fullmatch(r"库存\s*[:：]?\s*([0-9][0-9,]*)\s*(?:个|件|套|条|只|盒|对|包|袋|支|双|组|张|卷|箱|瓶|米)?", 库存原文 or "")
        规格.append({
            "name": 行["name"],
            "price_text": 价格原文,
            "purchase_price_cny": float(价格[1]) if 价格 else None,
            "stock_text": 库存原文,
            "stock": int(库存[1].replace(",", "")) if 库存 else None,
        })
    return {
        "source_url": 链接,
        "title": 购买区["title"],
        "sold_text": 已售,
        "minimum_order_quantity": 起批量,
        "minimum_order_unit": 起批单位,
        "minimum_order_text": 起批原文,
        "domestic_shipping": {"text": 运费, "amount_cny": 运费金额, "is_starting_price": 运费是起价},
        "skus": 规格,
    }


def 推断扩展名(链接: str, 内容类型: str) -> str:
    类型映射 = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
    类型 = 内容类型.split(";", 1)[0].lower()
    if 类型 in 类型映射:
        return 类型映射[类型]
    后缀 = Path(urlparse(链接).path).suffix.lower()
    return 后缀 if 后缀 in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"} else ".jpg"


def 下载单图(任务: dict, 请求头: dict, cookies: requests.cookies.RequestsCookieJar) -> str:
    # 每个线程使用独立的直连会话，确保图片下载不经过系统/环境代理。
    会话 = 创建直连会话()
    最后异常 = None
    for 尝试次数 in range(1, 下载重试次数 + 1):
        try:
            响应 = 会话.get(任务["链接"], headers=请求头, cookies=cookies, timeout=30)
            # 图片 CDN 有时会拒绝不属于当前域名的登录 Cookie。第一次请求
            # 仍保留浏览器 Cookie；遇到 400/420 时改用不带 Cookie 的直连请求。
            if 响应.status_code in {400, 420} and cookies:
                响应 = 会话.get(任务["链接"], headers=请求头, cookies={}, timeout=30)
            响应.raise_for_status()
            扩展名 = 推断扩展名(任务["链接"], 响应.headers.get("Content-Type", ""))
            路径 = 任务["目录"] / f'{任务["文件名"]}{扩展名}'
            if 扩展名 in {".jpg", ".jpeg", ".webp", ".png"}:
                with Image.open(io.BytesIO(响应.content)) as 图片:
                    if getattr(图片, "n_frames", 1) > 1:
                        路径.write_bytes(响应.content)
                        return str(路径)
                    缓冲区 = io.BytesIO()
                    if 扩展名 in {".jpg", ".jpeg"}:
                        if 图片.mode not in {"RGB", "L"}:
                            图片 = 图片.convert("RGB")
                        图片.save(缓冲区, format="JPEG", quality=90, optimize=True)
                    elif 扩展名 == ".webp":
                        图片.save(缓冲区, format="WEBP", quality=90)
                    else:
                        图片.save(缓冲区, format="PNG", optimize=True)
                    路径.write_bytes(缓冲区.getvalue())
            else:
                路径.write_bytes(响应.content)
            return str(路径)
        except requests.RequestException as 异常:
            最后异常 = 异常
            if 尝试次数 < 下载重试次数:
                print(f'下载重试：{任务["文件名"]}，第 {尝试次数 + 1}/{下载重试次数} 次……')
                time.sleep(下载重试间隔 * 尝试次数)
    raise 最后异常


def 批量下载(任务列表: list[dict], 页面) -> list[Path]:
    if not 任务列表:
        print("没有可下载的图片。")
        return []
    for 任务 in 任务列表:
        任务["目录"].mkdir(parents=True, exist_ok=True)
    请求头 = {
        "User-Agent": 页面.evaluate("navigator.userAgent"),
        "Referer": 页面.url,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }
    # 保留 Cookie 的域名和路径，避免把 1688.com 的 Cookie 错发给图片 CDN。
    cookies = requests.cookies.RequestsCookieJar()
    for 项 in 页面.context.cookies():
        if not all(ord(字符) <= 255 for 字符 in 项["value"]):
            continue
        参数 = {"path": 项.get("path") or "/"}
        if 项.get("domain"):
            参数["domain"] = 项["domain"]
        cookies.set(项["name"], 项["value"], **参数)
    已下载路径 = []
    with ThreadPoolExecutor(max_workers=下载线程数) as 线程池:
        映射 = {线程池.submit(下载单图, 任务, 请求头, cookies): 任务 for 任务 in 任务列表}
        for future in as_completed(映射):
            任务 = 映射[future]
            try:
                已下载路径.append(Path(future.result()))
                print(f'下载成功：{任务["文件名"]}')
            except Exception as 异常:
                print(f'下载失败：{任务["链接"]}，原因：{异常}')
    成功 = len(已下载路径)
    print(f"图片下载完成：成功 {成功} 张，失败 {len(任务列表) - 成功} 张。")
    return 已下载路径


def 清理已下载详情图(详情目录: Path, 新文件名: list[str]) -> None:
    """仅清理这次下载的新详情图，命中项进入 Windows 回收站。"""
    if not 新文件名:
        return
    清理器 = Path(__file__).resolve().with_name("详情图清理.py")
    OCR环境 = Path(os.environ["LOCALAPPDATA"]) / "Codex" / "1688-image-downloader" / "ocr-py312"
    Python命令 = ["py", "-3.12"] if OCR环境.is_dir() else [sys.executable]
    环境 = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with TemporaryDirectory(prefix="1688-detail-clean-") as 临时目录:
        路径 = Path(临时目录)
        文件清单 = 路径 / "downloaded.json"
        文件清单.write_text(json.dumps(sorted(set(新文件名)), ensure_ascii=False), encoding="utf-8")
        命令 = Python命令 + [
            "-u", str(清理器), str(详情目录.absolute()), "--only-files", str(文件清单),
            "--report", str(路径 / "cleanup.json"), "--apply",
        ]
        结果 = subprocess.run(命令, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=环境,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if 结果.stdout.strip():
            print(结果.stdout.rstrip())
        if 结果.returncode:
            问题 = 结果.stderr.strip().splitlines()
            raise RuntimeError(f"图片已下载，详情图清理未完成：{问题[-1] if 问题 else 结果.returncode}")


def 执行下载流程(
    链接: str,
    保存目录: str,
    等待验证=None,
    线程数: int = 8,
    重试次数: int = 3,
    使用指定目录: bool = False,
    文件名前缀: str = "",
) -> None:
    """执行一次下载任务，界面线程通过重定向标准输出显示运行日志。"""
    global 下载根目录, 下载线程数, 下载重试次数
    下载根目录 = Path(保存目录)
    下载线程数 = max(1, min(32, 线程数))
    下载重试次数 = max(1, min(10, 重试次数))

    print("1688 SKU、详情页图片与文本下载工具")
    print(f"保存根目录：{下载根目录}")
    启动浏览器()

    with sync_playwright() as p:
        浏览器 = p.chromium.connect_over_cdp(CDP地址)
        上下文 = 浏览器.contexts[0]
        页面 = 上下文.new_page()
        捕获数据 = []
        try:
            def 处理响应(响应: Response) -> None:
                地址 = 响应.url.lower()
                if 接口关键字 not in 地址 and 详情接口关键字 not in 地址:
                    return
                try:
                    数据 = 解析接口数据(响应.text())
                    if 提取SKU(数据):
                        if not 捕获数据:
                            print("已从网络接口捕获 SKU 数据。")
                        捕获数据.append(数据)
                except Exception:
                    # 同一接口可能包含重定向或被浏览器释放的中间响应，忽略即可。
                    pass

            页面.on("response", 处理响应)
            print("正在打开产品页面……")
            页面.goto(链接, wait_until="domcontentloaded", timeout=90000)
            页面.wait_for_timeout(3000)
            检查真人验证(页面, 等待验证)

            # 刷新一次可确保监听器捕获到页面初始化阶段的 SKU 请求。
            if not 捕获数据:
                print("尚未捕获 SKU 接口，正在刷新页面重试……")
                页面.reload(wait_until="domcontentloaded", timeout=90000)
                页面.wait_for_timeout(5000)
                检查真人验证(页面, 等待验证)

            标题 = 清理文件名(页面.title().replace("-阿里巴巴", "").strip(), "1688产品")
            文件名前缀 = 清理文件名(文件名前缀, "")
            文件名前缀 = f"{文件名前缀}_" if 文件名前缀 else ""
            # 界面模式按商品标题自动建目录；命令行模式直接使用用户提交的文件夹。
            产品目录 = 下载根目录 if 使用指定目录 else 下载根目录 / 标题
            sku目录 = 产品目录 / "sku"
            详情目录 = 产品目录 / "产品详情页"

            sku列表 = []
            sku来源 = ""
            for 数据 in reversed(捕获数据):
                sku列表 = 提取SKU(数据)
                if sku列表:
                    sku来源 = "网络接口"
                    break
            if not sku列表:
                sku列表 = 从页面内嵌数据提取SKU(页面)
                if sku列表:
                    sku来源 = "页面内嵌数据"
            if not sku列表:
                sku列表 = 从页面按钮提取SKU(页面)
                if sku列表:
                    sku来源 = "页面规格按钮"
            if sku列表:
                print(f"通过“{sku来源}”解析到 {len(sku列表)} 个带图片的 SKU。")
            else:
                print("多种方式均未找到带图片的 SKU。")
            购买区信息 = 提取购买区信息(页面, 链接)
            详情图 = 滚动加载详情图(页面, 等待验证)
            print(f"共找到 {len(详情图)} 张详情页图片。")
            等待商品详情内容(页面)
            详情文本 = 提取详情文本(页面)

            产品目录.mkdir(parents=True, exist_ok=True)
            商品信息文件 = 产品目录 / f"{文件名前缀}商品信息.json"
            商品信息文件.write_text(json.dumps(购买区信息, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            购买区规格 = 购买区信息["skus"]
            print(f"已保存购买区信息（{len(购买区规格)} 条 SKU）：{商品信息文件}")
            缺失项 = [名称 for 名称, 值 in (
                ("标题", 购买区信息["title"]),
                ("已售", 购买区信息["sold_text"]),
                ("起批量", 购买区信息["minimum_order_quantity"]),
                ("运费金额", 购买区信息["domestic_shipping"]["amount_cny"]),
            ) if 值 is None]
            if not 购买区规格 or any(None in (款["name"], 款["purchase_price_cny"], 款["stock"]) for 款 in 购买区规格):
                缺失项.append("SKU 名称、采购价或库存")
            if 缺失项:
                print(f"购买区有未读到的字段：{'、'.join(缺失项)}。")
            文本文件 = 产品目录 / f"{文件名前缀}产品文本介绍.txt"
            文本文件.write_text(详情文本 + ("\n" if 详情文本 else ""), encoding="utf-8-sig")
            if 详情文本:
                print(f"已保存产品网页文本，共 {len(详情文本)} 个字符：{文本文件}")
            else:
                print(f"#productPackInfo 和 #description 中没有网页文本，已生成空文本文件：{文本文件}")

            任务列表 = []
            已用名称 = {}
            for sku in sku列表:
                基础名 = 清理文件名(sku["名称"], "SKU")
                已用名称[基础名] = 已用名称.get(基础名, 0) + 1
                文件名 = 基础名 if 已用名称[基础名] == 1 else f"{基础名}_{已用名称[基础名]}"
                文件名 = f"{文件名前缀}{文件名}"
                任务列表.append({"链接": sku["图片"], "目录": sku目录, "文件名": 文件名})
            for 序号, 图片 in enumerate(详情图, 1):
                任务列表.append({"链接": 图片, "目录": 详情目录, "文件名": f"{文件名前缀}详情图_{序号:03d}"})

            已有详情图 = {文件.name for 文件 in 详情目录.iterdir()} if 详情目录.is_dir() else set()
            print(f"准备下载 {len(任务列表)} 张图片……")
            已下载图片 = 批量下载(任务列表, 页面)
            print(f"保存目录：{产品目录}")
            清理已下载详情图(详情目录, [
                文件.name for 文件 in 已下载图片
                if 文件.parent == 详情目录 and 文件.name not in 已有详情图
            ])
        finally:
            页面.close()
            浏览器.close()


class 日志重定向器:
    """把后台线程的输出安全地转发到界面文本框。"""

    def __init__(self, 回调):
        self.回调 = 回调

    def write(self, 内容):
        if 内容:
            self.回调(内容)

    def flush(self):
        pass


def 设置Windows高DPI适配():
    """在创建 Tk 窗口前启用每显示器高 DPI 感知，避免界面被系统模糊放大。"""
    if sys.platform != "win32":
        return
    try:
        # Windows 10 及更新版本：每显示器高 DPI 感知 V2。
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError, TypeError):
        pass
    try:
        # 兼容较早版本的 Windows。
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError, TypeError):
        pass


class 下载界面:
    """1688 图片下载桌面界面。"""

    def __init__(self, 根窗口):
        self.根窗口 = 根窗口
        self.根窗口.title("1688 产品图片下载")
        self.根窗口.geometry("900x650")
        self.根窗口.minsize(760, 560)
        self.根窗口.configure(bg="#f5f7fb")
        self.运行中 = False
        self.验证事件 = threading.Event()
        self.创建样式()
        self.创建界面()

    def 创建样式(self):
        样式 = ttk.Style()
        try:
            样式.theme_use("vista")
        except tk.TclError:
            pass
        样式.configure("标题.TLabel", font=("Microsoft YaHei UI", 20, "bold"), foreground="#16233b")
        样式.configure("说明.TLabel", font=("Microsoft YaHei UI", 10), foreground="#667085")
        样式.configure("主要.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 8))
        样式.configure("状态.TLabel", font=("Microsoft YaHei UI", 9), foreground="#667085")

    def 创建界面(self):
        外框 = ttk.Frame(self.根窗口, padding=24)
        外框.pack(fill="both", expand=True)

        ttk.Label(外框, text="1688 产品图片下载", style="标题.TLabel").pack(anchor="w")
        ttk.Label(外框, text="下载商品 SKU 图和产品详情页图片，并保存产品文字介绍。", style="说明.TLabel").pack(anchor="w", pady=(4, 18))

        参数框 = ttk.LabelFrame(外框, text="下载设置", padding=16)
        参数框.pack(fill="x")
        参数框.columnconfigure(1, weight=1)

        self.链接变量 = tk.StringVar(value=测试链接)
        self.目录变量 = tk.StringVar(value=str(下载根目录))
        self.线程变量 = tk.StringVar(value=str(下载线程数))
        self.重试变量 = tk.StringVar(value=str(下载重试次数))

        ttk.Label(参数框, text="1688 产品链接").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
        ttk.Entry(参数框, textvariable=self.链接变量).grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(参数框, text="保存根目录").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        ttk.Entry(参数框, textvariable=self.目录变量).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(参数框, text="选择目录", command=self.选择目录).grid(row=1, column=2, padx=(10, 0), pady=6)
        ttk.Label(参数框, text="并发下载数").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=6)
        ttk.Spinbox(参数框, from_=1, to=32, textvariable=self.线程变量, width=8).grid(row=2, column=1, sticky="w", pady=6)
        ttk.Label(参数框, text="失败重试次数").grid(row=2, column=2, sticky="e", padx=(10, 8), pady=6)
        ttk.Spinbox(参数框, from_=1, to=10, textvariable=self.重试变量, width=8).grid(row=2, column=3, sticky="w", pady=6)

        操作栏 = ttk.Frame(外框)
        操作栏.pack(fill="x", pady=(16, 12))
        self.开始按钮 = ttk.Button(操作栏, text="开始下载", style="主要.TButton", command=self.开始下载)
        self.开始按钮.pack(side="left")
        ttk.Button(操作栏, text="清空日志", command=self.清空日志).pack(side="left", padx=10)
        ttk.Button(操作栏, text="打开保存目录", command=self.打开目录).pack(side="left")
        self.状态变量 = tk.StringVar(value="就绪")
        ttk.Label(操作栏, textvariable=self.状态变量, style="状态.TLabel").pack(side="right")

        日志框 = ttk.LabelFrame(外框, text="运行日志", padding=8)
        日志框.pack(fill="both", expand=True)
        日志框.rowconfigure(0, weight=1)
        日志框.columnconfigure(0, weight=1)
        self.日志文本 = tk.Text(日志框, wrap="word", height=18, bg="#101828", fg="#d0d5dd", insertbackground="white", relief="flat", padx=12, pady=10, font=("Microsoft YaHei UI", 10))
        self.日志文本.grid(row=0, column=0, sticky="nsew")
        滚动条 = ttk.Scrollbar(日志框, orient="vertical", command=self.日志文本.yview)
        滚动条.grid(row=0, column=1, sticky="ns")
        self.日志文本.configure(yscrollcommand=滚动条.set, state="disabled")

    def 选择目录(self):
        目录 = filedialog.askdirectory(initialdir=self.目录变量.get(), title="选择图片保存根目录")
        if 目录:
            self.目录变量.set(目录)

    def 写日志(self, 内容):
        def 更新():
            self.日志文本.configure(state="normal")
            self.日志文本.insert("end", 内容)
            self.日志文本.see("end")
            self.日志文本.configure(state="disabled")
        self.根窗口.after(0, 更新)

    def 清空日志(self):
        self.日志文本.configure(state="normal")
        self.日志文本.delete("1.0", "end")
        self.日志文本.configure(state="disabled")

    def 打开目录(self):
        目录 = Path(self.目录变量.get()).expanduser()
        目录.mkdir(parents=True, exist_ok=True)
        os.startfile(str(目录))

    def 请求人工验证(self):
        self.验证事件.clear()

        def 提示():
            messagebox.showinfo("需要人工操作", "浏览器中出现登录、验证码或真人验证。\n\n请先在浏览器中完成操作，再点击“确定”继续。", parent=self.根窗口)
            self.验证事件.set()

        self.根窗口.after(0, 提示)
        self.验证事件.wait()

    def 开始下载(self):
        if self.运行中:
            return
        链接 = self.链接变量.get().strip()
        保存目录 = self.目录变量.get().strip()
        if not 链接:
            messagebox.showwarning("提示", "请输入 1688 产品链接。", parent=self.根窗口)
            return
        if not re.match(r"^https?://", 链接, re.I):
            messagebox.showwarning("提示", "链接应以 http:// 或 https:// 开头。", parent=self.根窗口)
            return
        try:
            线程数 = int(self.线程变量.get())
            重试次数 = int(self.重试变量.get())
            if not 1 <= 线程数 <= 32 or not 1 <= 重试次数 <= 10:
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "并发数应为 1-32，重试次数应为 1-10。", parent=self.根窗口)
            return

        self.运行中 = True
        self.开始按钮.configure(state="disabled")
        self.状态变量.set("正在下载……")
        self.写日志("\n" + "=" * 60 + "\n开始新的下载任务\n")

        def 工作线程():
            try:
                输出 = 日志重定向器(self.写日志)
                with redirect_stdout(输出):
                    执行下载流程(链接, 保存目录, self.请求人工验证, 线程数, 重试次数)
            except Exception as 异常:
                self.写日志(f"\n程序运行失败：{异常}\n")
            finally:
                self.根窗口.after(0, self.下载完成)

        threading.Thread(target=工作线程, daemon=True).start()

    def 下载完成(self):
        self.运行中 = False
        self.开始按钮.configure(state="normal")
        self.状态变量.set("任务完成，可开始下一次下载")


def 解析命令行参数(参数列表: list[str]):
    """解析命令行参数，同时支持位置参数和具名参数。"""
    解析器 = argparse.ArgumentParser(
        description="下载 1688 商品的 SKU 图、详情图、详情文本和购买区信息。完全不传参数时打开桌面界面。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            '  python "1688产品图片下载.pyw" "https://detail.1688.com/offer/123.html" "商品A"\n'
            r'  python "1688产品图片下载.pyw" --url "https://detail.1688.com/offer/123.html" --folder "D:\图片\商品A"'
        ),
    )
    解析器.add_argument("位置链接", nargs="?", help="1688 商品链接")
    解析器.add_argument(
        "位置保存文件夹",
        nargs="?",
        help="保存图片的文件夹名称或路径；相对路径位于脚本的“下载结果”目录下",
    )
    解析器.add_argument("-u", "--url", "--链接", dest="链接", help="1688 商品链接")
    解析器.add_argument(
        "-o", "--folder", "--output", "--保存文件夹",
        dest="保存文件夹",
        help="保存图片的文件夹名称或路径；技能仓库模式必须使用绝对路径",
    )
    解析器.add_argument(
        "-t", "--threads", "--线程数",
        dest="线程数", type=int, default=下载线程数, choices=range(1, 33), metavar="1-32",
        help=f"并发下载数（默认：{下载线程数}）",
    )
    解析器.add_argument(
        "-r", "--retries", "--重试次数",
        dest="重试次数", type=int, default=下载重试次数, choices=range(1, 11), metavar="1-10",
        help=f"下载失败重试次数（默认：{下载重试次数}）",
    )
    解析器.add_argument(
        "--prefix", "--前缀",
        dest="文件名前缀", default="",
        help="下载文件名前缀，例如：S0911-1",
    )
    参数 = 解析器.parse_args(参数列表)

    if 参数.位置链接 and 参数.链接:
        解析器.error("1688 链接请使用位置参数或 --url，不能重复提交。")
    if 参数.位置保存文件夹 and 参数.保存文件夹:
        解析器.error("保存文件夹请使用位置参数或 --folder，不能重复提交。")

    参数.链接 = 参数.链接 or 参数.位置链接
    参数.保存文件夹 = 参数.保存文件夹 or 参数.位置保存文件夹
    if not 参数.链接:
        解析器.error("缺少 1688 链接。")
    if not 参数.保存文件夹:
        解析器.error("缺少保存图片的文件夹名称或路径。")
    if os.environ.get("CODEX_SKILL_1688_USER_DATA", "").strip() and not Path(参数.保存文件夹).expanduser().is_absolute():
        解析器.error("技能仓库模式下 --folder 必须是绝对路径，避免把产物写入技能缓存。")
    if not re.match(r"^https?://", 参数.链接, re.I):
        解析器.error("1688 链接应以 http:// 或 https:// 开头。")
    return 参数


def 解析保存目录(文件夹: str) -> Path:
    """解析用户指定的保存目录；技能仓库模式不把产物写入可替换的缓存目录。"""
    目录 = Path(文件夹).expanduser()
    if not 目录.is_absolute():
        if os.environ.get("CODEX_SKILL_1688_USER_DATA", "").strip():
            raise ValueError("技能仓库模式下 --folder 必须使用绝对路径。")
        目录 = 下载根目录 / 目录
    return 目录.resolve()


def 运行命令行(参数列表: list[str]) -> int:
    """执行命令行下载，并用进程退出码表示成功或失败。"""
    参数 = 解析命令行参数(参数列表)
    保存目录 = 解析保存目录(参数.保存文件夹)
    try:
        执行下载流程(
            参数.链接,
            str(保存目录),
            None,
            参数.线程数,
            参数.重试次数,
            使用指定目录=True,
            文件名前缀=参数.文件名前缀,
        )
        return 0
    except KeyboardInterrupt:
        print("\n下载已由用户取消。", file=sys.stderr)
        return 130
    except Exception as 异常:
        print(f"\n程序运行失败：{异常}", file=sys.stderr)
        return 1


def 启动界面():
    设置Windows高DPI适配()
    根窗口 = tk.Tk()
    下载界面(根窗口)
    根窗口.mainloop()


def 主程序() -> int:
    # 保持原有体验：双击脚本或不带参数运行时，直接进入桌面界面。
    if len(sys.argv) == 1:
        if os.environ.get("CODEX_SKILL_1688_USER_DATA", "").strip():
            print("技能仓库模式需要通过命令行提供 --url 和绝对 --folder。", file=sys.stderr)
            return 2
        启动界面()
        return 0
    return 运行命令行(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(主程序())

