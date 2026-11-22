# 供水管网漏损抢修采录台

离线采录管段双测点压降、阀门隔离与抢修资质、住户通知及末端复水。校准和迟到复测改变疑似漏损管段；阀门拓扑、旁通和作业许可改变能否隔离；通知覆盖、独立末端压力与停水时长决定结案和补偿。每本账有自己的持久化实体、入口和反例；原始 JSON 文件字节与 SHA256 留在 SQLite，`audit` 在空库重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m waterrepair demo --db demo.sqlite
python -m waterrepair audit --db demo.sqlite
python -m unittest discover -s tests -v
```

CLI 入口 `segment-import`、`gauge-import`、`pressure-import`、`valve-import`、`permit-import`、`repair-import`、`reopen-import`、`notice-import`、`tap-import` 均接受 `file --db state.sqlite`；`restore-decide --db state.sqlite --repair R1` 追加结案决定。`fixtures` 含正常与会改变结论的合成反例。真实调压设备、GIS 拓扑、住户身份与法定赔付不在首版范围。

## 泵机健康判别与检修工单

低采样率波形会把高频轴承冲击折叠（混叠）成低频峰：20Hz 转轴在 200Hz 采样下，真正的一倍转频不平衡 `sin(2π·20n/200)` 与 180Hz 轴承冲击 `sin(2π·180n/200+π)` 在每个采样点数值完全相同。单段波形不可辨识，因此 `pump_health` 绝不把 FFT 最高峰解释成唯一故障或确定维修零件，而是：

- `health-assess --run PR1`：对每次运行产出**可证集合**（谱线、转速相干性等观察）与**不可辨识候选**（`unbalance_1x` 转子平衡 / `bearing_impact_aliased` 轴承，检修对象不同），并显式保存**混叠等价类**（所有采样后落到同一频点的源频率）和**需补采的记录器条件**（1kHz、抗混叠滤波、双轴、同步转速脉冲、原始字节与时钟）。
- `disposition-import`：按站点预先授权产生设备资格。`shutdown` 站点签拆检工单并把泵从备机名册移走（容量计 0），只有拆检找到滚道损伤才标定轴承故障；`hospital` 站点须机械主管 `signed_by` 签章，按 80% 限速轮值、备机容量只按限速能力计入，并自动预约 1kHz 双轴复采任务。同一份 200Hz 原件在两种站点得到不同资格，但都不宣称已证唯一故障。
- `recapture-import`：复采文件保存原始采样值、时钟起止、抗混叠滤波器参数、探头位置、负荷与同步转速脉冲，并与 `pump_run` 真实时段挂接。对丢包、时钟漂移、跨设备不同步输出置信界（含由漂移换算的频率不确定度），越界即判 `inadequate` 并开具体补采任务；合格时比较阶次谱与包络谱，证实 180Hz 冲击则升级轴承更换工单，反证则撤回**未执行**的更换单。
- `teardown-import`：登记拆检结果。已执行的停机、限速、拆检记录是不可改写的历史，反证不会把它们改成“从未执行”，只撤回尚未执行的更换单。
- `pump-health-report`：汇总评估、补采任务/记录、工单、拆检发现与按站备机容量；同一 `pump_id` 在名册中只有一行，不能在两个站点的可用容量里重复计数。

新增 CLI：`disposition-import`、`recapture-import`、`teardown-import`（均为 `file --db`），`health-assess --run`、`pump-health-report [--run]`。所有决定随原始字节入 `source` 表，`audit` 空库重放覆盖全部新实体。本模块只处理机械资产维护结论，不负责分区配水或住户账单。
