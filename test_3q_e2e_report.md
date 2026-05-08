# RetrieveAgent 3-Query E2E Report

## lite

| Q | Query | Latency | Focuses | TopK | Emitted | Merged | C/I/Noise | Sources | Angle MaxRedund | Schema | Error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Q44 | REDMAGIC 11 Pro 各站点首发销量和DTC渠… | 20.94s | 2 | 8 | 8 | 10 | 5/3/0 | bailian:1,feishu:4,google:3 | 2 | ✅ | - |
| Q51 | nubia 的产品和销售业务和销售额等的背景信息（近6个… | 15.05s | 3 | 12 | 10 | 10 | 5/5/0 | bailian:5,feishu:5 | 1 | ✅ | - |
| Q48 | REDMAGIC 品牌在游戏手机赛道的竞品格局和用户认知… | 21.51s | 4 | 16 | 12 | 12 | 5/7/0 | bailian:2,feishu:5,google:5 | 2 | ✅ | - |

### lite / Q44: REDMAGIC 11 Pro 各站点首发销量和DTC渠道表现

**focus_board**
```json
{
  "focus_0": {
    "status": "satisfied",
    "critical": 3,
    "important": 2,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "bailian",
      "feishu"
    ]
  },
  "focus_1": {
    "status": "satisfied",
    "critical": 4,
    "important": 5,
    "noise": 0,
    "contributors": [
      "feishu",
      "google"
    ],
    "targets": [
      "feishu",
      "google"
    ]
  }
}
```
angle_tag distribution: {'sales-performance': 2, 'sales-data': 1, 'conversion': 1, 'supply-chain': 1, 'sales-mix': 1, 'market-performance': 1, 'cvr-trend': 1}

**Sample chunks (top 5)**
1. 销售进度追踪 | 

产品:
渠道:EU
剩余库存:1515
剩余库存:1700
剩余库存:
累计销量:1770
11/25-12/1:59.00%
2. JP月会-2026年1月 | # DTC渠道&本地仓管理<mention-user id="ou_c4c9802979c7a379aebe394dd36a31df"/>

## 1月进度

### 红魔

<grid cols="2">

<column width="
3. Copy of [REDMAGIC] RM 11 Air Launch report | ## Sales Review - DTC Channel 2026.02.10 - 2026.03.11...
流量质量： 在流量激增的情况下，质量依然保持稳健且正向。

| BR Store | Sessions | ATC Rate 
4. JP月会-2026年1月 | # 项目进展（RM,nubia,ZTE,oladance）<mention-user id="ou_25af668039ab7d4de7a496446c4ccf3c"/>

### REDMAGIC

list{- 11 Pro 销售进展&
5. JP月会-2026年4月  | # DTC渠道&本地仓管理<mention-user id="ou_8be0a013db446a9a67e15e1882615b76"/>

### 3月中旬~4月上旬进度

#### 红魔

seq{1. 3/17~3/24 11Air 

### lite / Q51: nubia 的产品和销售业务和销售额等的背景信息（近6个月）

**focus_board**
```json
{
  "focus_0": {
    "status": "satisfied",
    "critical": 2,
    "important": 0,
    "noise": 0,
    "contributors": [
      "bailian"
    ],
    "targets": [
      "bailian",
      "google"
    ]
  },
  "focus_1": {
    "status": "satisfied",
    "critical": 2,
    "important": 4,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "bailian",
      "feishu"
    ]
  },
  "focus_2": {
    "status": "satisfied",
    "critical": 2,
    "important": 2,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "feishu",
      "bailian"
    ]
  }
}
```
angle_tag distribution: {'product-list': 1, 'launch-details': 1, 'sales-performance': 1, 'dtc-performance': 1, 'financial-summary': 1, 'sales-breakdown': 1, 'financial-settlement': 1, 'ops-optimization': 1, 'project-scope': 1, 'channel-management': 1}

**Sample chunks (top 5)**
1. nubia - EC Operation Calendar | 

 :Product Available
2025 Event Calendar:nubia Z70 Ultra
nubia Z60 Ultra LV & nubia Z60S Pro
nubia Z60 Ultra
nubia Flip
2. ZTE项目概况 | 

Teasing:2025/6/5Pre-order:2025/6/15(赠送保护壳)●Open-sales:2025/6/24Launch站点:EU, UK, GL (不含US,仅指GL-other regions) , MX●特殊情况
3. 2024年全球高端智能手机市场竞争格局与 nubia Z60 Ultra 运营全链路战略分析报告AI-Gen | # 第一部分：公司内部运营情况与 nubia Z60 Ultra 首发效能深度剖析

nubia Z60 Ultra 的全球发布策略采取了与中国国内市场同步上市的激进节奏 ()。这一策略在行业内较为罕见，成功打破了传统中国出海品牌全球发布滞
4. ZTE 业务总结报告 (2025-12-29 ~ 2026-01-04) | 

本周DTC渠道进入典型的促销后期调整阶段, Net Sales Amount 录得 $26,354(环比-32.8%), Net Sales Volume为45(环比-42.3%) 。伴随Total Coupon Amount同步下降4
5. NUBIA 业务总结报告 (2026-01-15 ~ 2026-01-21) | 

NUBIA业务总结报告(2026-01-15~2026-01-21)报告周期:2026-01-15~2026-01-21 vs. 2026-01-08~2026-01-14温馨提醒:本报告由 Fastlane Data AgentTM自

### lite / Q48: REDMAGIC 品牌在游戏手机赛道的竞品格局和用户认知度

**focus_board**
```json
{
  "focus_0": {
    "status": "noise_only",
    "critical": 1,
    "important": 2,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "bailian",
      "feishu"
    ]
  },
  "focus_1": {
    "status": "noise_only",
    "critical": 0,
    "important": 3,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "bailian",
      "feishu"
    ]
  },
  "focus_2": {
    "status": "satisfied",
    "critical": 2,
    "important": 2,
    "noise": 0,
    "contributors": [
      "bailian",
      "google"
    ],
    "targets": [
      "bailian",
      "google"
    ]
  },
  "focus_3": {
    "status": "satisfied",
    "critical": 3,
    "important": 2,
    "noise": 0,
    "contributors": [
      "feishu",
      "google"
    ],
    "targets": [
      "feishu",
      "google"
    ]
  }
}
```
angle_tag distribution: {'spec-detail': 2, 'user-feedback': 2, 'competitor-analysis': 1, 'brand-positioning': 1, 'target-persona': 1, 'sales-data': 1, 'brand-awareness': 1, 'competitor': 1, 'performance-comparison': 1, 'market-reception': 1}

**Sample chunks (top 5)**
1. 🇰🇷 红魔 (REDMAGIC) 韩国市场 GTM 方案 | # 市场目标 (What is Success?)

### 2.1 上市产品 (2026.Q1) & 竞品分析

#### REDMAGIC 11pro (游戏手机)

list{- **定位：** 旗舰级专业电竞手机。; - **核心优
2. 墨西哥区域平板价格对比 | 

品牌:Redmagic
产品名称:Redmagic Nova Gaming Tablet
内存:12+256
KSP:8 GEN 3 leading version chipset
10.9" 2.8K display
10100mAh
3. Copy of [REDMAGIC] RM 11 Air Launch report | ## Voice of Customer (VOC) Summary...
User feedback highlights a successful shift toward a "premium" brand identity, but
4. 🇰🇷 红魔 (REDMAGIC) 韩国市场 GTM 方案 | list{- **画像：** 16-25 岁，学生群体。; - **行为：** T1 (Faker) 和 LCK 的忠实粉丝。不一定每天玩高强度游戏，但追求“电竞美学” (Gamer Aesthetic)。; - **消费心理：** “偶像
5. 不太正常的红魔问题回答（销售） | | 排名 | 机型 | 销量 | 占比 |
| --- | --- | --- | --- |
| 1 | REDMAGIC 10 Pro | 1,161件 | 44.4% |
| 2 | （未命名SKU） | 1,144件 | 43.7%

## balanced

| Q | Query | Latency | Focuses | TopK | Emitted | Merged | C/I/Noise | Sources | Angle MaxRedund | Schema | Error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Q44 | REDMAGIC 11 Pro 各站点首发销量和DTC渠… | 20.33s | 2 | 8 | 8 | 11 | 6/2/0 | bailian:1,feishu:3,google:4 | 1 | ✅ | - |
| Q51 | nubia 的产品和销售业务和销售额等的背景信息（近6个… | 16.13s | 3 | 12 | 9 | 9 | 7/2/0 | bailian:5,feishu:4 | 2 | ✅ | - |
| Q48 | REDMAGIC 品牌在游戏手机赛道的竞品格局和用户认知… | 24.46s | 4 | 16 | 11 | 12 | 5/6/0 | bailian:1,feishu:5,google:5 | 2 | ✅ | - |

### balanced / Q44: REDMAGIC 11 Pro 各站点首发销量和DTC渠道表现

**focus_board**
```json
{
  "focus_0": {
    "status": "satisfied",
    "critical": 3,
    "important": 2,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "bailian",
      "feishu"
    ]
  },
  "focus_1": {
    "status": "satisfied",
    "critical": 3,
    "important": 5,
    "noise": 0,
    "contributors": [
      "feishu",
      "google"
    ],
    "targets": [
      "feishu",
      "google"
    ]
  }
}
```
angle_tag distribution: {'sales-data': 1, 'regional-sales': 1, 'gmv-sales': 1, 'conversion-metrics': 1, 'inventory-performance': 1, 'conversion-rate': 1, 'sales-breakdown': 1, 'traffic-analysis': 1}

**Sample chunks (top 5)**
1. 销售进度追踪 | 

产品:
渠道:JP [链接](https://docs.google.com/spreadsheets/d/1SXIt8LgLMAXZa1dYM6fRcFDdv94Eqk-F_JjJkaAX5FA/edit?usp=sharing)
剩
2. REDMAGIC 业务总结报告 (2025-12-22 ~ 2025-12-28) | | 店铺 | 净销量 | 净销售额 | 成功订单数 | 客单价 |
| --- | --- | --- | --- | --- |
| RedMagic EU | 1,108 (-0.7%) | $464,379.87 (+7.7%) | 
3. Copy of [REDMAGIC] RM 11 Air Launch report | ## Sales Review 2026.02.10 - 2026.03.11...
|  |  |  |  |  |
|---|---|---|---|---|
| Channel | Target | Sales / Qty | Sal
4. REDMAGIC 业务总结报告 (2025-12-22 ~ 2025-12-28) | | 产品 | 浏览数 | 加购数 | 加购率 | 支付订单数 | 支付转化率 |
| --- | --- | --- | --- | --- | --- |
| REDMAGIC 11 Pro | 154,954 (+12.0%) | 12
5. REDMAGIC 业务总结报告 (2025-12-22 ~ 2025-12-28) | | 产品 | 近2周净销量 | DOS | 风险提示 | 总库存数量 | 可用库存数量 |
| --- | --- | --- | --- | --- | --- |
| REDMAGIC 11Pro | 2435 | 51.0 | 健康 

### balanced / Q51: nubia 的产品和销售业务和销售额等的背景信息（近6个月）

**focus_board**
```json
{
  "focus_0": {
    "status": "satisfied",
    "critical": 2,
    "important": 0,
    "noise": 0,
    "contributors": [
      "bailian"
    ],
    "targets": [
      "bailian",
      "google"
    ]
  },
  "focus_1": {
    "status": "satisfied",
    "critical": 4,
    "important": 0,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "bailian",
      "feishu"
    ]
  },
  "focus_2": {
    "status": "satisfied",
    "critical": 2,
    "important": 2,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "feishu",
      "bailian"
    ]
  }
}
```
angle_tag distribution: {'sales-performance': 2, 'product-launch': 1, 'spec-detail': 1, 'channel-operation': 1, 'dtc-operations': 1, 'financial-data': 1, 'amazon-operations': 1, 'sales-analysis': 1}

**Sample chunks (top 5)**
1. nubia - EC Operation Calendar | 

 :New Product Launch
2025 Event Calendar:
2025 Event Calendar:
2025 Event Calendar:
2025 Event Calendar:nubia Z70S Ult
2. nubia Z80U竞品对比 | 

参数:国际发布
nubia Z80 Ultra:2025-11-01
nubia Z70 Ultra:2024-11-01
Xiaomi 15:2025-03 (MWC)
OnePlus 13:2025-01-07
realme GT7
3. NUBIA 业务总结报告 (2026-01-15 ~ 2026-01-21) | 

Current status(2025.01.15-2025.01.21)| 2026.01.15-2025.01.21|2026.01.15-2025.01.21|2026.01.15-2025.01.21|2026.01.15-20
4. NUBIA 业务总结报告 (2026-01-15 ~ 2026-01-21) | 

| 店铺|净销量|净销售额|优惠金额|成功订单数|客单价|毛销量|毛销售额|
| NubiaAlphaEU|33(-10.8%)|$15,596.50|$48.73(-89.7%)|32(-3.0%)|516.97(-33.2%)|34
5. NUBIA 业务总结报告 (2026-01-29 ~ 2026-02-04) | | 指标 | 本周期数据 |
| --- | --- |
| 净销量 | 19 (-47.2%) |
| 净销售额 | $10,824.66 (-31.8%) |
| 毛销量 | 24 (-35.1%) |
| 毛销售额 | $14,421

### balanced / Q48: REDMAGIC 品牌在游戏手机赛道的竞品格局和用户认知度

**focus_board**
```json
{
  "focus_0": {
    "status": "satisfied",
    "critical": 2,
    "important": 1,
    "noise": 1,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "bailian",
      "feishu"
    ]
  },
  "focus_1": {
    "status": "noise_only",
    "critical": 1,
    "important": 3,
    "noise": 0,
    "contributors": [
      "bailian",
      "feishu"
    ],
    "targets": [
      "bailian",
      "feishu"
    ]
  },
  "focus_2": {
    "status": "noise_only",
    "critical": 1,
    "important": 1,
    "noise": 0,
    "contributors": [
      "google"
    ],
    "targets": [
      "bailian",
      "google"
    ]
  },
  "focus_3": {
    "status": "noise_only",
    "critical": 1,
    "important": 3,
    "noise": 0,
    "contributors": [
      "feishu",
      "google"
    ],
    "targets": [
      "feishu",
      "google"
    ]
  }
}
```
angle_tag distribution: {'customer-feedback': 2, 'competitor-analysis': 1, 'market-share': 1, 'user-persona': 1, 'spec-comparison': 1, 'loyalty-data': 1, 'market-survey': 1, 'brand-awareness': 1, 'marketing-points': 1, 'growth-metrics': 1}

**Sample chunks (top 5)**
1. 🇰🇷 红魔 (REDMAGIC) 韩国市场 GTM 方案 | | 竞品 | 2026 预期型号 | 竞争优势 | 竞争劣势 (红魔的机会点) |
| --- | --- | --- | --- |
| 三星 | Galaxy S26 Ultra | 品牌 (90% 忠诚度)、拍照、生态、AS 售后 |
2. 🇰🇷 红魔 (REDMAGIC) 韩国市场 GTM 方案 | ## 市场背景 (Why Korea?) > 1.2 电竞 & 手机行业分析

**[Table from: 🇰🇷 红魔 (REDMAGIC) 韩国市场 GTM 方案]**
| 电竞与游戏市场 (机遇) | 智能手机市场 (挑战) |
| 
3. 🇰🇷 红魔 (REDMAGIC) 韩国市场 GTM 方案 | list{- **画像：** 16-25 岁，学生群体。; - **行为：** T1 (Faker) 和 LCK 的忠实粉丝。不一定每天玩高强度游戏，但追求“电竞美学” (Gamer Aesthetic)。; - **消费心理：** “偶像
4. Red Magic 3 - KM-0412 | ## 竞品排查对比...
| 规格项 | Red Magic 3 | Black Shark 2 | Asus ROG 2 | Vivo IQOO |
|---|---|---|---|---|
| 发布 | 2019.04.25 | 20
5. [REDMAGIC] RM 11 Air Launch report | ## Loyalty Users Performance...
| RM VIP Code used | 1 h | 14 h | EA | Final | VIP Users |
|---|---|---|---|---|---|
| E
