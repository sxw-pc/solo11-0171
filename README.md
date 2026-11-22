# 供水管网漏损抢修采录台

离线采录管段双测点压降、阀门隔离与抢修资质、住户通知及末端复水。校准和迟到复测改变疑似漏损管段；阀门拓扑、旁通和作业许可改变能否隔离；通知覆盖、独立末端压力与停水时长决定结案和补偿。每本账有自己的持久化实体、入口和反例；原始 JSON 文件字节与 SHA256 留在 SQLite，`audit` 在空库重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m waterrepair demo --db demo.sqlite
python -m waterrepair audit --db demo.sqlite
python -m unittest discover -s tests -v
```

CLI 入口 `segment-import`、`gauge-import`、`pressure-import`、`valve-import`、`permit-import`、`repair-import`、`reopen-import`、`notice-import`、`tap-import` 均接受 `file --db state.sqlite`；`restore-decide --db state.sqlite --repair R1` 追加结案决定。`fixtures` 含正常与会改变结论的合成反例。真实调压设备、GIS 拓扑、住户身份与法定赔付不在首版范围。

## 泵机改造验收与补测规划

`pump_acceptance.py` 在 `pump_run`/`pump_test` 原始测点之上建验收链，不改动 `performance.points` 的只录原始测点语义，也不参与住户水费分摊。

- 单点不能签参考工况结论。改造后 250 kPa、72000 L、24000 Wh 测点与至少两条物理可行泵曲线相容：恒损耗外推在 300 kPa 同流量给 25000 Wh（达标），恒效率比例外推给 28800 Wh（不达标）。可行参考耗电区间 `[25000, 28800] Wh` 横跨 25500 Wh 保证门槛时，`evaluate` 只返回 `indeterminate` 与补测规划，任何 Wh/L 节能率或单点比例公式都不签发设备状态。
- 补测规划给出安全扬程上限（`safe_max_head_kpa`）内歧义最小的补测扬程，并列出可直接判合格的耗电上沿、可直接判不合格的耗电下沿与两者之间的待定带；落进待定带的读数保留待测，不靠平均强行签发。新增可信测点把整个可行区间压到门槛以下才升级正式证书。
- 两份验收授权各自独立：厂家参考点保证条款在未证明 300 kPa 前只发 `trial` 试运行资格并保留承包商补测义务；市政现场节能试行条款只就已测的 250 kPa 工况核定 `30000→24000 Wh`（20%）现场节能，发 `limited_field` 限定工况投用证，证书文字明确不得转写成厂家 300 kPa 保证履约。
- 证据链按表计（电、流量、上下游压头、调速）录入 `test_evidence`，强制稳定工况窗口覆盖测点、窗口落在运行时段内并排除启动瞬态。共母线电表必须有 `bus_allocation` 分摊：读数乘分摊份额等于泵本体耗电，其他并联泵与变频器损耗不得计入 P1。
- 校准更正 `calibration-correction-import` 可追溯废止历史读数；凡证据链依赖该表的已发布证书都追加 `withdrawn` 撤证版本（原版本保留不删改），非废止性复校不撤证。`acceptance-publish --case AC1` 幂等发布，`acceptance-report` 汇总可行区间、两份授权结论、补测规划与证据缺陷；全部新实体随 `audit` 在空库逐源重放，合成撤证来源由校准更正确定性派生。

新增 CLI：`acceptance-case-import`、`meter-import`、`bus-allocation-import`、`test-evidence-import`、`calibration-correction-import` 接受 `file --db`；`acceptance-publish --db --case`、`acceptance-report --db`。
