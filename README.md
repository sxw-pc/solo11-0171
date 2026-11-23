# 供水管网漏损抢修采录台

离线采录管段双测点压降、阀门隔离与抢修资质、住户通知及末端复水。校准和迟到复测改变疑似漏损管段；阀门拓扑、旁通和作业许可改变能否隔离；通知覆盖、独立末端压力与停水时长决定结案和补偿。每本账有自己的持久化实体、入口和反例；原始 JSON 文件字节与 SHA256 留在 SQLite，`audit` 在空库重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m waterrepair demo --db demo.sqlite
python -m waterrepair audit --db demo.sqlite
python -m unittest discover -s tests -v
```

CLI 入口 `segment-import`、`gauge-import`、`pressure-import`、`valve-import`、`permit-import`、`repair-import`、`reopen-import`、`notice-import`、`tap-import` 均接受 `file --db state.sqlite`；`restore-decide --db state.sqlite --repair R1` 追加结案决定。`fixtures` 含正常与会改变结论的合成反例。真实调压设备、GIS 拓扑、住户身份与法定赔付不在首版范围。

## 施工路权与开挖许可

`route_permit` 在维修隔离（repair 处于 `isolated`）之后对开挖施工单独裁决，主营结果是可以合法开工的路线、占用地块、许可机关及开工窗口。地籍边界、地下燃气管线、路障、通道宽度、设备回转范围、道路临时封闭许可与私人地役权均按原始 CRS、测绘精度与法律生效版本采录：

- 以整条开挖带构造施工占用面（路径缓冲矩形 + 关节外接八边形，保守外包），不做中心线点判断；open-cut 占整条开挖带，directional-drill 只占出入土基坑；开挖带加设备回转构成安全包络，用于燃气安全带与路障判定。
- 地块边界带测绘误差时按 ±precision 侵蚀/膨胀给出三值判定：definite（确定占用）/uncertain（待复测）/none（确定不占）；不确定且无权源一律 `resurvey`，不挑有利地图。
- 候选路线 = corridor × method_spec，同受起终点、最小转弯半径、挖机宽度、封路窗口与同意版本约束；共享同一车道封闭名额按工点计数，不能逐张独立发许可。
- 权源按 地块+工点+方法+窗口 匹配：道路封闭许可（closure）、私人限界地役权（easement，含赔付条款）、土地同意书（land_consent）；open-cut 同意书不能给顶管用，权利凭证不能跨工点挪用。
- 撤销旧测绘（`survey-revoke`）、迟到燃气图、权人撤回同意（`easement-withdraw`）后：已开工事实（`route-start-import`）保留，未开工许可撤回或重审并给出替代路线。
- 输出可核对的几何交集、权利链、机关、时间与费用责任；无可行路线时明确列出冲突证据。

新增 CLI：`parcel-import`、`corridor-import`、`gas-line-import`、`obstacle-import`、`closure-import`、`easement-import`、`method-spec-import`、`survey-revoke`、`easement-withdraw`、`route-start-import` 均接受 `file --db`；`route-permit-decide --db state.sqlite --repair R1 --at 2026-07-01T06:00:00Z` 追加开挖裁决，`route-permit-report --db state.sqlite` 查看许可卷宗。坐标基准不一致时裁决拒绝（`crs_conflict`）。
