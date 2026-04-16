"""业务关键词泛化集 — 中英文同义词 / 等价词映射

来源：
  - llm_classifier.py 的 CATEGORY_REF（l2 业务领域关键词）
  - 实际检索 chunk 样本中观察到的中英混排模式

外部入口函数：
  expand_keywords(words)             — 扩展关键词集，加入已知同义词
  get_generalization_entry(term)     — 查看某个词的全部等价词
  add_generalization(term, synonyms) — 动态新增/扩展条目（测试用）
  list_all_terms()                   — 列出所有收录主词（debug 用）
  show_coverage_for_focuses(focuses) — 展示 focus 列表中哪些词会被扩展（调试）
"""

from typing import Dict, Set

# ─────────────────────────────────────────────────────────────────────────────
# 主映射表：term (小写) → set of synonyms
# ─────────────────────────────────────────────────────────────────────────────
_KW_GENERALIZATION: Dict[str, Set[str]] = {

    # ── 地区（国家缩写 ↔ 中文全称 ↔ 英文全称，三向互查）──────────────────────
    "eu":    {"欧洲", "europe", "european", "欧区"},
    "欧洲":  {"eu", "europe", "european"},
    "europe":{"eu", "欧洲", "european"},
    "us":    {"美国", "america", "united states", "美区"},
    "美国":  {"us", "america", "united states"},
    "united states": {"us", "美国", "america"},
    "uk":    {"英国", "britain", "england", "united kingdom"},
    "英国":  {"uk", "britain", "united kingdom"},
    "united kingdom": {"uk", "英国", "britain"},
    "britain": {"uk", "英国", "united kingdom"},
    "sg":    {"新加坡", "singapore"},
    "新加坡": {"sg", "singapore"},
    "singapore": {"sg", "新加坡"},
    "mx":    {"墨西哥", "mexico"},
    "墨西哥": {"mx", "mexico"},
    "mexico": {"mx", "墨西哥"},
    "sa":    {"沙特", "saudi", "saudi arabia"},
    "沙特":  {"sa", "saudi", "saudi arabia"},
    "saudi arabia": {"sa", "沙特"},
    "uae":   {"阿联酋", "emirates", "dubai", "united arab emirates"},
    "阿联酋": {"uae", "emirates", "united arab emirates"},
    "united arab emirates": {"uae", "阿联酋"},
    "kr":    {"韩国", "korea", "south korea"},
    "韩国":  {"kr", "korea", "south korea"},
    "south korea": {"kr", "韩国"},
    "br":    {"巴西", "brazil"},
    "巴西":  {"br", "brazil"},
    "brazil": {"br", "巴西"},
    "gl":    {"全球", "global", "worldwide"},
    "全球":  {"global", "gl", "worldwide"},
    "global": {"全球", "gl", "worldwide"},
    "ca":    {"加拿大", "canada"},
    "加拿大": {"ca", "canada"},
    "canada": {"ca", "加拿大"},
    "au":    {"澳大利亚", "australia"},
    "澳大利亚": {"au", "australia"},
    "australia": {"au", "澳大利亚"},
    "jp":    {"日本", "japan"},
    "日本":  {"jp", "japan"},
    "japan": {"jp", "日本"},
    "de":    {"德国", "germany"},
    "德国":  {"de", "germany"},
    "germany": {"de", "德国"},
    "fr":    {"法国", "france"},
    "法国":  {"fr", "france"},
    "france": {"fr", "法国"},
    "it":    {"意大利", "italy"},
    "意大利": {"it", "italy"},
    "italy": {"it", "意大利"},

    # ── 时间 / 季度 ───────────────────────────────────────────────────────────
    "q1":     {"第一季度", "一季度", "jan", "feb", "mar", "1月", "2月", "3月"},
    "q2":     {"第二季度", "二季度", "apr", "may", "jun", "4月", "5月", "6月"},
    "q3":     {"第三季度", "三季度", "jul", "aug", "sep", "7月", "8月", "9月"},
    "q4":     {"第四季度", "四季度", "oct", "nov", "dec", "10月", "11月", "12月"},
    "第一季度": {"q1", "一季度"},
    "第二季度": {"q2", "二季度"},
    "第三季度": {"q3", "三季度"},
    "第四季度": {"q4", "四季度"},
    "一季度": {"q1", "第一季度"},
    "二季度": {"q2", "第二季度"},
    "三季度": {"q3", "第三季度"},
    "四季度": {"q4", "第四季度"},

    # ── 流量指标（DTC / Marketplace 通用）────────────────────────────────────
    "流量":   {"traffic", "session", "会话", "访客", "uv", "pv", "访问量", "访问"},
    "traffic":{"流量", "session", "会话", "访客", "uv", "访问"},
    "session":{"会话", "流量", "访问", "sessions"},
    "访客":   {"visitor", "uv", "unique visitor", "流量"},
    "uv":     {"访客", "用户数", "unique visitor"},
    "pv":     {"页面浏览", "page view", "浏览量"},
    "展示量": {"impression", "曝光量", "impressions"},
    "impression": {"展示量", "曝光量", "impressions"},
    "曝光":   {"impression", "展示", "曝光量"},

    # ── 转化指标 ──────────────────────────────────────────────────────────────
    "转化率": {"cvr", "conversion rate", "conversion", "购买率"},
    "cvr":    {"转化率", "conversion rate", "购买率"},
    "购买率": {"cvr", "转化率", "order rate", "purchase rate"},
    "加购率": {"add to cart rate", "加购转化", "atc rate"},
    "点击率": {"ctr", "click through rate", "click rate"},
    "ctr":    {"点击率", "click through rate"},
    "加购":   {"add to cart", "atc", "加入购物车"},

    # ── 广告指标 ──────────────────────────────────────────────────────────────
    "roas":   {"广告回报", "广告roi", "return on ad spend", "广告回报率"},
    "广告回报": {"roas", "roi"},
    "cpm":    {"千次展示费用", "cost per mille", "千次曝光"},
    "cpc":    {"单次点击费用", "cost per click", "点击成本"},
    "广告":   {"ads", "advertising", "投放", "付费推广"},
    "ads":    {"广告", "advertising", "投放"},
    "投放":   {"广告", "ads", "paid media"},

    # ── 销售指标 ──────────────────────────────────────────────────────────────
    "gmv":    {"销售额", "成交额", "revenue", "营收", "销售总额"},
    "销售额": {"gmv", "revenue", "成交额", "收入", "net sales"},
    "revenue":{"销售额", "gmv", "营收", "收入"},
    "净销量": {"销量", "出货量", "units sold", "net sales volume"},
    "出货量": {"净销量", "units sold", "销量"},
    "库存":   {"inventory", "stock", "在库量", "可用库存"},
    "dos":    {"库存周转", "days of stock", "库存天数"},
    "库存周转":{"dos", "days of stock", "库存天数"},
    "退货":   {"return", "退换货", "售后", "refund", "returns"},

    # ── 渠道 ─────────────────────────────────────────────────────────────────
    "dtc":    {"独立站", "直销", "direct", "官网", "自有渠道", "direct-to-consumer"},
    "独立站": {"dtc", "direct", "官网", "自有渠道"},
    "marketplace": {"平台", "亚马逊", "amazon", "第三方平台", "电商平台"},
    "亚马逊": {"amazon", "amz", "marketplace"},
    "amazon": {"亚马逊", "amz", "marketplace"},
    "affiliate":   {"联盟", "联盟营销", "awin", "admitad", "cps"},
    "联盟":   {"affiliate", "awin", "admitad", "cps", "联盟营销"},
    "联盟营销": {"affiliate marketing", "affiliate", "联盟"},
    "店铺":   {"store", "shop", "site", "官网", "网站"},

    # ── KOL / 达人 ────────────────────────────────────────────────────────────
    "kol":    {"达人", "influencer", "博主", "网红", "youtuber", "tiktoker"},
    "达人":   {"kol", "influencer", "博主", "网红"},
    "influencer": {"kol", "达人", "博主", "网红"},

    # ── 社媒 / 邮件 ───────────────────────────────────────────────────────────
    "edm":    {"邮件营销", "newsletter", "email", "邮件"},
    "邮件":   {"email", "edm", "newsletter"},
    "newsletter": {"邮件", "edm", "email"},
    "社媒":   {"social media", "社交媒体"},
    "pr":     {"公关", "媒体", "press release"},

    # ── 搜索 / SEO ────────────────────────────────────────────────────────────
    "搜索":   {"search", "organic search", "seo", "自然搜索"},
    "search": {"搜索", "seo"},
    "seo":    {"搜索优化", "自然搜索", "organic search"},

    # ── 渠道分类词（Q1 Organic Search/Paid Ads/Direct 等）────────────────────
    "organic search": {"自然搜索", "seo", "搜索", "organic"},
    "paid ads":       {"付费广告", "ads", "广告", "paid", "cpc", "cpm"},
    "paid":           {"付费", "付费广告", "paid ads"},
    "direct":         {"直接访问", "直接流量", "直接"},
    "influencers":    {"达人", "kol", "influencer", "网红", "博主"},
    "email & sms":    {"邮件", "edm", "sms", "短信", "newsletter"},
    "sms":            {"短信", "短信营销", "email"},
    "organic social": {"自然社媒", "社媒", "social media", "organic"},
    "referrer":       {"引荐", "来源", "外链", "referral"},
    "referrer domains": {"引荐域", "外链来源", "referral", "引荐"},
    "渠道":           {"channel", "来源", "source", "acquisition"},
    "channel":        {"渠道", "来源", "source", "acquisition"},

    # ── 热卖 / 产品表现词（Q8 热卖产品/出单）────────────────────────────────
    "热卖":    {"bestseller", "best seller", "畅销", "热销", "top selling"},
    "畅销":    {"热卖", "bestseller", "best seller", "热销"},
    "热销":    {"热卖", "畅销", "bestseller"},
    "出单":    {"下单", "order", "orders", "购买", "purchase", "成交"},
    "下单":    {"出单", "order", "购买", "purchase"},
    "订单量":  {"orders", "order count", "成交量", "出单数"},
    "出单渠道": {"销售渠道", "channel", "购买来源", "order source"},
    "店铺表现": {"store performance", "业绩", "销售表现", "经营指标"},
    "业绩":    {"performance", "表现", "销售额", "revenue"},
    "经营":    {"运营", "operation", "business", "business performance"},
    "热门":    {"popular", "trending", "热卖", "畅销"},

    # ── 财务 / 业务分析词（Q7 业财角度）─────────────────────────────────────
    "业务":    {"business", "运营", "经营", "operation"},
    "财务":    {"finance", "financial", "revenue", "profit", "cost"},
    "财务角度": {"financial perspective", "财务分析", "financial analysis"},
    "盈利":    {"profit", "盈亏", "净利", "margin"},
    "成本":    {"cost", "花费", "expense", "支出"},
    "利润":    {"profit", "margin", "净利润", "盈利"},
    "痛点":    {"pain point", "问题", "挑战", "challenge", "issue"},
    "亮点":    {"highlight", "优点", "成就", "achievement"},
    "项目特点": {"project feature", "项目亮点", "特色", "特点"},

    # ── 通用运营动词 ──────────────────────────────────────────────────────────
    "复盘":   {"review", "总结", "汇报", "recap", "回顾"},
    "增长":   {"growth", "增幅", "增速", "环比增长", "上涨"},
    "下降":   {"decline", "降低", "减少", "下滑"},
    "周报":   {"weekly report", "weekly", "weekly summary"},
    "月报":   {"monthly report", "monthly"},
    "季报":   {"quarterly report", "quarterly", "季度报告"},

    # ── 时间粒度（Q6/Q7 月度/全年对比）──────────────────────────────────────
    "月度":   {"monthly", "月份", "month", "按月"},
    "月份":   {"monthly", "month", "月度"},
    "全年":   {"annual", "yearly", "full year", "年度", "yoy"},
    "年度":   {"annual", "yearly", "full year", "全年"},
    "同比":   {"yoy", "year over year", "year-on-year", "年同比"},
    "环比":   {"mom", "month over month", "qoq", "链比"},
    "yoy":    {"同比", "年同比", "year over year"},
    "mom":    {"环比", "month over month"},

    # ── GA4 / 流量结构（Q7）──────────────────────────────────────────────────
    "ga4":       {"google analytics", "analytics", "谷歌分析", "ga"},
    "流量结构":  {"流量来源", "traffic source", "channel", "渠道来源", "流量渠道", "acquisition"},
    "流量来源":  {"流量结构", "traffic source", "channel", "acquisition"},
    "organic":   {"自然流量", "自然搜索", "organic search", "自然"},
    "自然流量":  {"organic", "organic search", "自然搜索"},
    "direct":    {"直接访问", "direct traffic", "直接流量"},
    "直接访问":  {"direct", "direct traffic"},
    "referral":  {"引荐", "外链", "referrer", "来源链接"},
    "引荐":      {"referral", "referrer", "外链"},
    "paid social": {"付费社交", "facebook ads", "meta ads"},
    "付费社交":  {"paid social", "facebook ads", "meta"},
    "cross-network": {"跨网络", "performance max", "pmax"},

    # ── 产品维度（Q8 产品线占比）─────────────────────────────────────────────
    "spu":     {"产品", "product", "型号", "款式", "sku", "产品型号"},
    "产品线":  {"product line", "product family", "spu", "品类"},
    "占比":    {"proportion", "percentage", "share", "比例", "构成"},
    "构成":    {"composition", "占比", "breakdown", "拆分"},
    "net sales": {"净销售额", "净销额", "净销量"},
    "净销售额": {"net sales", "net revenue", "净销额"},
    "净销额":  {"net sales", "净销售额"},

    # ── 排除模式规范化（通用范式）────────────────────────────────────────────
    "除":      {"except", "excluding", "不含", "排除", "扣除", "去掉", "不包括"},
    "不含":    {"除", "except", "excluding", "排除", "不包括"},
    "排除":    {"除", "except", "excluding", "不含", "去除"},
    "不包括":  {"除", "except", "excluding", "不含", "排除"},
    "except":  {"除", "excluding", "不含", "排除", "不包括"},
    "excluding": {"除", "except", "不含", "排除"},
    "扣除":    {"除", "不含", "排除"},
    "去掉":    {"除", "不含", "排除", "去除"},
    "去除":    {"除", "不含", "排除", "去掉"},
    "only":    {"仅", "只", "仅限", "只含"},
    "仅":      {"only", "只", "仅限", "just"},
    "包含":    {"including", "含", "含有", "include"},
    "including": {"包含", "含", "含有"},
}

# ── 构建反向索引（synonym → 主词）────────────────────────────────────────────
_REVERSE_MAP: Dict[str, Set[str]] = {}
for _term, _synonyms in _KW_GENERALIZATION.items():
    for _syn in _synonyms:
        _s = _syn.lower()
        if _s not in _REVERSE_MAP:
            _REVERSE_MAP[_s] = set()
        _REVERSE_MAP[_s].add(_term)


# ─────────────────────────────────────────────────────────────────────────────
# 公开入口函数
# ─────────────────────────────────────────────────────────────────────────────

def expand_keywords(words: Set[str], passes: int = 2) -> Set[str]:
    """
    将关键词集合扩展：加入所有已知等价词/同义词（小写，双向查找）。
    [P1.6] 支持多轮扩展：第一轮扩展后的新词再做一轮，捕获传递性同义词链。
    """
    expanded = {w.lower() for w in words}
    for _ in range(passes):
        new_words: Set[str] = set()
        for w in expanded:
            if w in _KW_GENERALIZATION:
                new_words |= {s.lower() for s in _KW_GENERALIZATION[w]}
            if w in _REVERSE_MAP:
                new_words |= {s.lower() for s in _REVERSE_MAP[w]}
        if not (new_words - expanded):  # 收敛，提前退出
            break
        expanded |= new_words
    return expanded


def get_generalization_entry(term: str) -> Set[str]:
    """查询一个词的全部等价词（正向 + 反向查找）。"""
    result: Set[str] = set()
    t = term.lower()
    if t in _KW_GENERALIZATION:
        result |= _KW_GENERALIZATION[t]
    if t in _REVERSE_MAP:
        result |= _REVERSE_MAP[t]
    return result


def add_generalization(term: str, synonyms: Set[str]) -> None:
    """动态新增/扩展泛化条目（运行时有效，重启后失效）。
    测试稳定后请写回 _KW_GENERALIZATION 常量。
    """
    t = term.lower()
    if t not in _KW_GENERALIZATION:
        _KW_GENERALIZATION[t] = set()
    _KW_GENERALIZATION[t] |= synonyms
    for syn in synonyms:
        s = syn.lower()
        if s not in _REVERSE_MAP:
            _REVERSE_MAP[s] = set()
        _REVERSE_MAP[s].add(t)


def list_all_terms() -> list:
    """列出所有已收录主词（debug / 维护用）。"""
    return sorted(_KW_GENERALIZATION.keys())


def show_coverage_for_focuses(focuses: list) -> dict:
    """展示 focus 列表中哪些词会被泛化扩展（调试用）。

    Returns:
        {原始词: 扩展词集合}
    """
    import re
    result = {}
    for focus in focuses:
        tokens = re.findall(r'[\w\u4e00-\u9fff]+', focus.lower())
        for token in tokens:
            expansions = get_generalization_entry(token)
            if expansions:
                result[token] = expansions
    return result
