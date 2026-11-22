# 供水管网漏损抢修采录台

离线采录管段双测点压降、阀门隔离与抢修资质、住户通知及末端复水。校准和迟到复测改变疑似漏损管段；阀门拓扑、旁通和作业许可改变能否隔离；通知覆盖、独立末端压力与停水时长决定结案和补偿。每本账有自己的持久化实体、入口和反例；原始 JSON 文件字节与 SHA256 留在 SQLite，`audit` 在空库重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m waterrepair demo --db demo.sqlite
python -m waterrepair audit --db demo.sqlite
python -m unittest discover -s tests -v
```

CLI 入口 `segment-import`、`gauge-import`、`pressure-import`、`valve-import`、`permit-import`、`repair-import`、`reopen-import`、`notice-import`、`tap-import` 均接受 `file --db state.sqlite`；`restore-decide --db state.sqlite --repair R1` 追加结案决定。`fixtures` 含正常与会改变结论的合成反例。真实调压设备、GIS 拓扑、住户身份与法定赔付不在首版范围。
