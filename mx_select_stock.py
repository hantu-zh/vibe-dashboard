# -*- coding: utf-8 -*-
"""
mx_select_stock - 妙想智能选股 API Python 封装
"""
import os, json, ssl, urllib.request
from typing import List, Dict, Tuple, Optional

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

API_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/stock-screen"


def _get_apikey() -> str:
    key = os.environ.get("MX_APIKEY", "")
    if not key:
        raise RuntimeError("MX_APIKEY 环境变量未设置")
    return key


def _split_val(v) -> str:
    """提取字段值（去除 | 后缀和额外信息）"""
    if v is None:
        return ""
    s = str(v).strip()
    if "|" in s:
        s = s.split("|")[0].strip()
    return s


def _make_col_map(columns: List[Dict]) -> Dict[str, str]:
    """建立 key -> title 的映射"""
    return {col["key"]: col["title"] for col in columns}


def _make_row(columns: List[Dict], raw: Dict) -> Dict[str, str]:
    """将一行业务数据映射为 {title: value} 字典"""
    col_map = _make_col_map(columns)
    out = {}
    for key, title in col_map.items():
        out[title] = _split_val(raw.get(key, ""))
    return out


class MXSelectStock:
    """妙想智能选股 API 封装"""

    def search(self, keyword: str, page: int = 1, page_size: int = 100) -> Dict:
        """
        执行选股查询，返回原始 JSON 响应。
        """
        apikey = _get_apikey()
        payload = json.dumps({
            "keyword": keyword,
            "pageNo": page,
            "pageSize": page_size
        }).encode("utf-8")

        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "apikey": apikey,
                "User-Agent": "Mozilla/5.0",
            }
        )
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def extract_data(self, result: Dict) -> Tuple[List[Dict], List[Dict], Optional[str]]:
        """
        从 search() 返回的 JSON 中提取列定义和行数据。

        Returns:
            rows: List[Dict] – 每行 {title: value}，列名使用中文标题
            columns: List[Dict] – 列定义 [{key, title, unit, ...}]
            err: Optional[str] – 错误信息，无错为 None
        """
        try:
            inner = result.get("data", {}).get("data", {})
            ar = inner.get("allResults", {})
            res = ar.get("result", {})
            cols = res.get("columns", [])
            data_list = res.get("dataList", [])

            rows = [_make_row(cols, raw) for raw in data_list]
            return rows, cols, None
        except Exception as e:
            return [], [], str(e)
