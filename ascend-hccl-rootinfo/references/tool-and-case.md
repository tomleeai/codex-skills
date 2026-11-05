# 工具来源与单机适配

本技能的辅助脚本适配 PyPI `unofficial-ascend-tools==0.0.11`，其中提供名为 `mindcluster-tools` 的 CLI。它是非官方发行包；发行包版本为 `0.0.11`，CLI 内部版本曾显示 `0.1.0`，不能混为同一版本。

- 包页面：<https://pypi.org/project/unofficial-ascend-tools/0.0.11/>
- 已核对 wheel：`unofficial_ascend_tools-0.0.11-py3-none-any.whl`
- SHA256：`42420d0c4fefe27e2f5d97940f322672968deefd3952704abdbc42d639d0d1c5`
- 昇腾文档列出该资源文件用途：<https://www.hiascend.com/document/detail/en/mindcluster/2600/clustersched/schedulingug/docs/en/scheduling/appendix.md>

使用现场已经审阅且校验 hash 的 wheel；若要重新获取，先核实来源和版本，再在容器内用隔离环境安装。例如：

```bash
sha256sum /tmp/unofficial_ascend_tools-0.0.11-py3-none-any.whl
python3 -m venv /opt/hccl-rootinfo-tools
/opt/hccl-rootinfo-tools/bin/python -m pip install --no-index --no-deps \
  /tmp/unofficial_ascend_tools-0.0.11-py3-none-any.whl
```

镜像需要有 venv/pip，设备需要 DCMI 的库和权限，驱动拓扑路径也要在容器内可读。不要为了装包自动扩大容器权限。用户指定的容器启动命令已映射设备时，复用该配置。

## 为何使用单机分支

包内 `rankinfo/npu.py` 默认在 mainboard 39、super_pod_type 3 时用 Server Index 计算 local_id。Server Index 未设置而返回 65535 时，此计算产生非法值。默认拓扑还会选 FM16 的 `atlas_850_2.json`。

包内另有 `product_server.process_npu_for_server2` / `process_level0`：它从实时 URMA 设备中选择 die 1 的 mesh 端口，按单机设备数生成 local_id。本技能在已确认的单机八卡拓扑上复用此代码，使用原 `RootInfoEncoder` 序列化，不手写新的 JSON schema，也不篡改硬件返回值。

辅助脚本只承诺这项受限适配；它不安装包、不拉起服务、不修改网络或设备设置。它核对拓扑端口、实时 EID 和输出编号，不证明链路实时 UP。HCCL 初始化和权重读取仍需服务日志给出证据。

## 现场案例的证据边界

2026-10-21 的同宿主现场，修正后的文件为 version 2.0、rank_count 8、local_id 0..7、atlas_850_1、第 0 层每卡七个 EID。8 卡 DP/EP 实例所有 rank 初始化成功，并开始读取 48 个权重分片。

相同文件在另一容器中的 SHA256 一致，不能把后续不同启动结果归因于文件不同：四卡 EP 的模型创建曾在约 62.89 GiB 已分配时 OOM，八卡 EP 才进入权重读取。成功案例只证明该配置越过初始化和模型创建，不证明完整服务健康或 4+4 PD 分离。

本技能随后在同镜像的全新容器中完成了独立前后验证：固定同一份八卡启动脚本，初始无 rootinfo 时自动探测产生越界 local_id 并启动失败；在容器内运行本技能脚本生成文件后，原脚本再次启动，两个八 rank communicator 的所有 rank 均成功初始化，并实际读取了第一个权重分片。最终版生成脚本重复生成的候选文件与运行中使用的文件逐字一致。验收止于权重读取阶段。
