# JoyAI-Video-Edit 可重复部署脚本与文档

此目录对应 2026-10-07 已成功运行的 RTX PRO 6000 Blackwell 96GB 服务。
默认公网地址为 https://joyai.nuvatech.cn/ 。本次只导出脚本和文档，不重新安装当前服务器。

## 1. 下次如何使用

在本地 Linux、macOS 或 Windows WSL 中进入本目录。需要 Bash、OpenSSH、curl和Python3；运行本地检查还需要Git。
先按新服务器信息修改 `config.env`；其中没有密钥。

```bash
cd /home/ctyun/nuva/JoyAI-Video-Edit/deploy/kit
bash deploy.sh help

# 首次连接新服务器，核对平台提供的 SSH 主机指纹，并建立免密登录。
ssh -p 15298 root@connect.westd.seetacloud.com
ssh-copy-id -p 15298 root@connect.westd.seetacloud.com

# 直接上传原始文件，创建独立环境，下载并校验权重，编译、启动、真实推理验收。
bash deploy.sh gpu

# 不配置域名也可以使用：保持此命令运行，浏览器打开 localhost:18080。
bash deploy.sh tunnel
```

脚本、文档、版本约束和下载清单直接存放在 `deploy/kit/` 中，无需解压。
源码、示例输入、参考图和许可证随指定Git提交下载；YOLO检测模型在安装时自动导出。
将完整 `deploy/kit/` 目录复制到其他电脑后，也可进入该目录运行相同命令。
连接信息以 `config.env` 为准，上面的 SSH 示例是本次服务器。
脚本后续连接使用 `StrictHostKeyChecking=yes`，不会自动接受未知主机。
`gpu` 安装需要较长时间，建议在本地 `tmux` 会话中执行；失败后可修正网络并重跑，下载会断点续传。

## 2. 环境和容量

| 项目 | 固定配置 / 要求 |
|---|---|
| GPU | NVIDIA RTX PRO 6000 Blackwell，96GB，sm_120a |
| 系统 | Linux x86_64；已验证 Ubuntu 22.04.5 AutoDL 容器；GPU 端使用 root |
| 驱动 | 必须由平台预装并支持 Blackwell / CUDA 12.8；本次为 595.71.05 |
| Python | 独立 Conda 环境，3.10.22 |
| PyTorch / torchvision | 2.9.1+cu128 / 0.24.1+cu128 |
| CUDA / nvcc | CUDA 12.8；nvcc 12.8.93 |
| Transformers / Diffusers | 4.57.6 / 0.36.0 |
| 推理 | FP8 image/text、CUDA Graph、cuDNN attention、1248×720、目标16 FPS |
| 持久盘 | 推荐至少200GB；核心权重约51GB，另需环境、wheel、编译缓存和录制空间 |
| 内存 | 推荐至少100GB可用容器内存；以容器限额为准，不以宿主机 `free` 数字为准 |
| 网络 | 下载阶段需访问清华/阿里云镜像、ModelScope、Hugging Face、GitHub |

项目使用 PyTorch，无需 TensorFlow。保留服务器原有基础环境；已有 `/root/miniconda3` 会直接复用。
没有 Conda 时，脚本安装经 SHA256 校验的 Miniconda py310 24.5.0。
本次已有 Conda 为24.4.0，Conda自身版本不决定模型性能。
这些脚本针对 PRO 6000，其他GPU需按上游部署指南调整。

GPU 上固定路径如下，换服务器时仍使用这些路径：

```text
/root/autodl-tmp/JoyAI-Video-Edit     项目
/root/autodl-tmp/joyai-env           独立Python/CUDA环境
/root/autodl-tmp/joyai-wheels        下载的安装包
/root/autodl-tmp/joyai-detector-export  独立YOLO导出环境与权重缓存
/root/autodl-tmp/joyai-logs          服务日志
/root/autodl-tmp/joyai-recordings    输入/编辑结果录制
JoyAI-Video-Edit/deploy/deps/checkpoints       权重
JoyAI-Video-Edit/deploy/deps/cache_pro6000     编译缓存
```

若平台没有 `/root/autodl-tmp` 持久盘，应先挂载持久盘或统一调整脚本中的路径。
容器内不能通过本脚本安装/更换宿主 GPU 驱动。

## 3. 公网域名和HTTPS

AutoDL GPU容器没有独立公网IP。本次使用阿里云测试网关提供公网入口：

```text
浏览器 → HTTPS Nginx → 网关127.0.0.1:18180
       → SSH隧道 → GPU127.0.0.1:8080
```

`config.env` 中分别填写 GPU SSH、网关 SSH、公网 IPv4、域名和网关TLS文件路径。
网关要求已经装好 Nginx、OpenSSH 和 systemd，并且已经有覆盖该域名的有效证书。
新网关先核对SSH主机指纹并配置免密登录；阿里云安全组需允许80/443访问。

```bash
# 单独设置指定主机名的AliDNS A记录。密钥只在本地读取。
bash deploy.sh dns

# 创建专用隧道密钥，授权GPU端口转发，安装Nginx和systemd配置。
bash deploy.sh public
```

DNS需要等待缓存生效；在GPU健康且DNS正确时，`public` 最后会验证公网HTTPS健康接口。
若最后公网检查失败，网关配置仍保留，可按故障排查章节确认DNS/安全组，不必重装GPU。
网关脚本只修改该域名的Nginx文件、`joyai-tunnel.service` 和 `/etc/joyai/`。
Nginx语法检查失败时恢复原站点配置。不会修改其他站点或生产服务器。

`dns` 从 `DNS_ENV_FILE` 读取 `OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`，
账号需要对应域名的AliDNS读写权限；OSS权限本身不等于DNS权限。
本次域名已指向 `47.103.55.27`，不需要再次修改。
脚本不会上传 `.env` 或导出SSH私钥；网关隧道密钥在网关现场生成，
GPU授权仅允许转发 `127.0.0.1:8080`，禁止交互式Shell。

网关TLS证书不会自动申请或续期。本次已有证书到期时间为2026-12-07，需要在网关续期。
若换成其他域名，请相应提供证书和DNS权限；`alidns.py`针对一层子域名，如 `joyai.nuvatech.cn`。

## 4. 启动和日常管理

```bash
bash deploy.sh status
bash deploy.sh start
bash deploy.sh restart
bash deploy.sh smoke
bash deploy.sh tunnel
```

`status` 同时检查进程和模型健康；仅Supervisor显示Running不代表模型已经加载好。
`restart` 返回后模型仍可能正在预热；可再次执行 `status` 或在GPU执行下面的 `wait`。

```bash
ssh -p 15298 root@connect.westd.seetacloud.com
bash /root/autodl-tmp/JoyAI-Video-Edit/deploy/ops/manage.sh wait
tail -f /root/autodl-tmp/joyai-logs/demo.log
```

AutoDL已有Go Supervisor时使用它，显式控制地址为 `127.0.0.1:19090`。
普通Ubuntu没有Go Supervisor时，使用独立Conda环境中的Python Supervisor 4.3.0。
两种方式都启动本项目独立实例，不修改平台Supervisor。

容器停机再开或重建后，持久盘保留模型和缓存，但需执行 `bash deploy.sh start`。
网关隧道由systemd管理，开机自动启动，连接中断后自动重连。

```bash
# 网关排查
ssh -p 2222 root@47.103.55.27
systemctl status joyai-tunnel
journalctl -u joyai-tunnel -n 50
nginx -t
```

## 5. 安装过程与复现说明

1. 检查root、架构、PRO 6000及现有服务，验证部署脚本和下载清单SHA256。
2. 从官方GitHub获取并检出提交 `ca17e1d1030f454cb98b0ed549b4d31a60139ceb`，创建独立Python3.10.22环境。
3. 按manifest下载PyTorch/CUDA/runtime wheel，固定SHA256并支持续传。
4. 安装CUDA12.8编译器及开发头文件，按 `requirements-lock.txt` 约束实际运行依赖。
5. 获取CUTLASS固定提交 `dcf215af68a2d08d305076c152a06f201728cd53`，只编译sm_120a的FP8算子。
6. 从官方ModelScope下载DiT、VAE和MiMo四分片，校验六个大文件的官方SHA256；HF只取小配置/tokenizer。
7. 下载YuNet和官方YOLOv8n权重，校验权重SHA256，在独立环境CPU导出ONNX，验证静态输入和主环境OpenCV输出 `(1,84,2100)`。
8. 启动官方demo，完成横竖屏VAE、参考图形状和完整流水线预热，等待 `/health`。
9. 发送17帧真实输入，验证3个输出块、非空720p JPEG、正数DiT耗时和第三块CUDA Graph执行，再检查健康。

SageAttention和FA4没有安装：官方在PRO 6000上推荐普通cuDNN。
上游demo代码保持不变；自动启动与网络配置均放在 `deploy/ops/`。
YOLO导出使用独立venv，继承主环境PyTorch，新增依赖只安装在导出环境中：
`ultralytics==8.4.39`、`onnx==1.19.1`、`onnxslim==0.1.71`、`onnxruntime==1.23.2`、
`opencv-python==4.13.0.92`，并沿用主环境版本约束。
导出时隐藏GPU并关闭自动安装依赖，固定320×320、opset12、静态形状、CPU导出和简化。
主环境中的有效ONNX可直接复用；删除该文件后重跑导出脚本会重新生成。

```bash
# 在GPU执行；首次安装自动执行，无需手动调用。
bash /root/autodl-tmp/JoyAI-Video-Edit/deploy/ops/export-detector.sh
```

权重来源：[Ultralytics assets v8.4.0](https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt)，
SHA256为 `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36`。
YOLO使用[Ultralytics AGPL-3.0许可证](https://github.com/ultralytics/ultralytics/blob/v8.4.39/LICENSE)。
不要把导出依赖安装进模型运行环境，避免改动OpenCV等依赖。

## 6. 常见问题

- 首次启动可能长时间没有HTTP端口。PyTorch autotune/编译较慢，完整首次预热可能超过上游120秒期限。
  Supervisor会在进程失败后重新启动并复用缓存。保留缓存，查看日志并等到 `Application startup complete`。
  `gpu` 默认最多等待一小时；调整GPU端 `JOYAI_START_TIMEOUT` 可延长等待。
- 下载慢或失败：核对镜像连通性，AutoDL可利用已有 `/etc/network_turbo` 下载小文件。
  重跑可续传；不要删除已验证的51GB权重。阿里云wheel下载使用pip User-Agent以避免403。
- `/health` 显示 `restart required`：这是上游会话清理超时，进程可能仍运行。
  执行 `restart`、等待模型健康，再启动新会话；当前脚本不做额外的健康监控自动重启。
- 摄像头必须在HTTPS或localhost使用，并允许浏览器摄像头权限。实体摄像头未在此次验收中测试。
- 官方demo同一时间处理一个活跃会话，其他用户排队。提示词增强外部API未配置，使用原始提示词。
- 公网无登录门槛，上传/录制文件会占用GPU盘空间；按需要管理录制目录。
- 多卡可放置VAE不同阶段或启动多个实例增加并发；官方没有现成DiT张量并行。
  官方最高公布单卡基准为B200 720p/30FPS，PRO 6000为720p/16FPS；这些不是本次端到端持续性能测试。

## 7. 文件与验证范围

本目录只保留部署脚本、说明、版本约束、校验清单和本地检查。
源码、默认验收输入和网页参考图由官方固定Git提交提供；ONNX由官方权重现场导出。
源码下载使用临时目录，成功后才放到项目目录；失败可重跑且不会覆盖其他目录。
历史验收结果归纳在本文中，不保存可重新生成的JSON输出文件。
两份Supervisor配置分别负责主配置和程序配置，通过 `include` 引用，均需保留。

| 文件 | 用途 |
|---|---|
| `deploy.sh` / `config.env` | 本地统一入口 / 无密钥连接配置 |
| `ops/bootstrap.sh` | GPU首次安装全流程 |
| `ops/source.sh` / `ops/export-detector.sh` | 固定提交源码下载 / 独立环境YOLO导出与验证 |
| `ops/install.sh` / `ops/download.sh` | 固定环境与FP8编译 / 权重下载校验 |
| `ops/serve.sh` / `ops/manage.sh` / `ops/*.conf` | 官方服务启动 / 进程与健康管理 |
| `ops/gateway.sh` / `ops/alidns.py` | HTTPS网关和SSH隧道 / 指定DNS记录管理 |
| `ops/smoke.py` | 真实推理和CUDA Graph验收 |
| `ops/requirements-lock.txt` / `ops/*downloads.txt` | 实际版本约束 / 下载来源与SHA256 |
| `SHA256SUMS` | 上传脚本和下载清单的文件校验和 |
| `verify.sh` / `selftest.py` | 无网络、无部署副作用的本地脚本检查 |

本次实际服务已验证：HTTPS、WSS、SSH转发、8秒MP4上传编辑播放下载、参考图编辑、
桌面/手机布局、受控重启、重启后再次生成17帧且CUDA Graph生效。
原验收脚本在现有公网服务生成17帧720p输出，确认第三块CUDA Graph执行，
清理后再次取得会话并收到pong，记录 `session_reuse: true`。
当前精简脚本通过13项本地检查，覆盖源码固定提交、重复执行、下载失败清理及并发目录保护。
源码安装检查需要Linux；在macOS运行 `verify.sh` 时跳过这4项GPU端检查。
已实际从官方GitHub下载固定提交；在GPU服务器的临时独立环境中完成官方权重下载、
CPU导出、ONNX检查及主环境OpenCV推理检查，输出为 `(1,84,2100)`。
没有在第二台空白GPU服务器重新下载并全量部署。

```bash
bash verify.sh

# 在Linux修改ops脚本后，重新生成上传文件校验清单。
find ops -type f ! -path '*/__pycache__/*' -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
```

此目录不含主模型权重、Python环境、缓存、私钥或 `.env`。
安装时在线下载源码、依赖和权重；本地存储与GPU上传均使用原始脚本，不使用压缩包。
