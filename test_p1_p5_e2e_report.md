# P1-P5 Multi-Source E2E Report

## balanced

| Q | Latency | Focuses | TopK cap | Emitted | Merged | C/I/Noise | Sources | Max Angle Redundancy | Schema OK | Error |
|---|---|---|---|---|---|---|---|---|---|---|
| Q50 | 27.92s | 3 | 12 | 10 | 10 | 6/4/0 | feishu:5,google:5 | 1 | ✅ | - |

### Q50 focus_board
```json
{
  "focus_0": {
    "status": "satisfied",
    "critical": 2,
    "important": 2,
    "noise": 0,
    "contributors": [
      "feishu",
      "google"
    ],
    "targets": [
      "google",
      "feishu"
    ]
  },
  "focus_1": {
    "status": "satisfied",
    "critical": 3,
    "important": 1,
    "noise": 0,
    "contributors": [
      "feishu",
      "google"
    ],
    "targets": [
      "google",
      "feishu"
    ]
  },
  "focus_2": {
    "status": "satisfied",
    "critical": 2,
    "important": 1,
    "noise": 0,
    "contributors": [
      "feishu",
      "google"
    ],
    "targets": [
      "google",
      "feishu"
    ]
  }
}
```
angle_tag distribution: {'financial-report': 1, 'sales-performance': 1, 'operational-metrics': 1, 'order-metrics': 1, 'conversion-funnel': 1, 'conversion-rate': 1, 'regional-sales': 1, 'store-performance': 1, 'traffic-metrics': 1, 'financial-data': 1}
