# OutlookAutomation

基于 Python + Patchright + PySide6 的 Windows 桌面自动化程序。

已实现 v1.0 → v1.4：浏览器管理、配置系统、账号管理、流程引擎（含验证码自动按压）、
任务队列与 Worker 池、**PySide6 桌面 GUI**、代理与环境（Profile）管理、
**GitHub Actions 自动编译 Windows 安装包**。

## 给最终用户

从 [Releases](https://github.com/zhonggy/out-exe/releases) 下载
`OutlookAutomation-Setup.exe`（约 289MB），安装后双击桌面图标即可。
不需要安装 Python、Node.js，也不需要手动下载 Chromium。

- 程序装在 `C:\Program Files\OutlookAutomation`（只读）
- 用户数据在 `%APPDATA%\OutlookAutomation`（卸载默认保留，升级不覆盖）
- 首次运行会被 SmartScreen 拦（未做代码签名），点「更多信息」→「仍要运行」

升级：开「关于与更新」页 → 检查更新 → 下载更新 → 立即重启并更新。
覆盖安装不会动用户数据（账号、数据库、登录态 Profile）。

## 给开发者

```bash
pip install -r requirements-dev.txt
patchright install chromium
python scripts/setup_browser.py    # 下载 fingerprint-chromium 指纹内核（约 180MB）

python main.py doctor              # 环境自检
python main.py gui                 # 启动桌面 GUI
```

> 指纹内核体积大不入仓库。`setup_browser.py` 默认下载**锁定版本**（`PINNED_TAG`）
> 而非 latest，保证 CI 构建可重现；国内网络会自动探测本机代理端口（7897/7890/10809）。
> 内核路径由 `browser/kernel.py` 自动定位，配置里写 `fingerprint` 关键字即可，
> 内核升级换目录名也不用改配置。

创建 `accounts.txt`（每行一条，默认分隔符 `----`）：

```
account1@example.com----password1
account2@example.com----password2
```

然后在 GUI 里导入账号、派发任务、点开始。也可以走命令行：

```bash
python main.py import              # 导入账号到 SQLite
python main.py run --limit 10      # 跑 10 个任务（跑完退出）
python main.py work                # 常驻执行进程（GUI 用的就是这个）
```

## 进程模型

**GUI 进程不启动浏览器。** 所有浏览器任务在独立执行进程中运行：

```
OutlookAutomation.exe            ← GUI（PySide6）
  │
  ├─ 写 SQLite（下单 / 导入账号 / 取消）
  │
  └─ subprocess.Popen → OutlookAutomation.exe --exec-worker --workers N
                            │   ← 同一个 EXE，argv 分流
                            ├─ TaskManager + Worker 线程 × N
                            ├─ Chromium × N
                            └─ 命名管道 → 回推日志 / 进度 / 状态
```

这样设计的原因：

- Patchright 同步 API 底层是 greenlet，与 Qt 事件循环放同一进程会产生难调试的冲突
- 浏览器崩溃、指纹内核 DLL 异常只会带走执行进程，GUI 仍可用、能重启
- 关闭窗口不中断任务（要停止请在「任务管理」页点「停止执行」）

SQLite（WAL）是两个进程的共享真相源；命名管道只负责展示层的低延迟推送，
断开后 GUI 自动退回轮询 `worker_stats.json`，不影响任务正确性。

## 命令行

| 命令 | 说明 |
| --- | --- |
| `python main.py gui` | 启动桌面 GUI |
| `python main.py work` | 常驻执行进程（`--workers`） |
| `python main.py import` | 导入 `accounts.txt`（`--file` 指定其他文件） |
| `python main.py run` | 执行任务并等待完成（`--limit` / `--account` / `--type` / `--workers`） |
| `python main.py stats` | 账号 / 任务 / 验证码 / 代理 / 环境统计 |
| `python main.py accounts` | 列出账号（`--status NEW`） |
| `python main.py tasks` | 列出任务记录 |
| `python main.py export` | 导出结果 CSV（不含密码） |
| `python main.py clean` | 清理（`--profiles` / `--tasks` / `--all-profiles`） |
| `python main.py doctor` | 依赖与运行环境自检 |

打包后同一个 EXE 由 argv 分流：无参数 → GUI，`--exec-worker` → 执行进程。

## 目录结构

```
OutlookAutomation/
├── main.py                  入口（GUI / --exec-worker / CLI 子命令）
├── desktop/                 PySide6 桌面 GUI
│   ├── app.py               QApplication 引导 + argv 分流
│   ├── main_window.py       主窗口 + 左侧导航 + 统一刷新定时器
│   ├── context.py           AppContext，各页共享的运行时单例
│   ├── single_instance.py   单实例锁（防双开抢同一个数据库）
│   ├── theme.py             深色主题 + 字体度量（不写死像素）
│   ├── bridge/
│   │   ├── worker_proc.py   执行进程生命周期 + argv 构造
│   │   ├── ipc.py           命名管道 IPC（行分隔 JSON）
│   │   └── tasks.py         QThreadPool 后台任务
│   └── views/               9 个页面（仪表盘/账号/任务/日志/浏览器/Profile/代理/设置）
│       ├── widgets.py       通用组件 + 按字体计算尺寸的构造器
│       └── flow_layout.py   自动换行的工具栏布局
├── config/
│   ├── config.yaml          全部可调参数
│   └── loader.py            YAML 加载 + 点号访问 + 环境变量覆盖
├── browser/
│   ├── browser.py           BrowserSession，浏览器生命周期
│   ├── context.py           启动参数 / 反检测 / 视口 / 地区
│   ├── profile.py           ProfileManager，环境目录分配与回收
│   └── manager.py           BrowserManager，活跃实例登记
├── account/
│   ├── manager.py           导入 / 分配 / 状态回写 / 导出
│   └── status.py            页面状态 → 账号状态判定
├── flow/
│   ├── base.py              BaseFlow + 流程注册表
│   ├── detector.py          页面状态检测（只读）
│   ├── action.py            页面操作 + 人类化鼠标轨迹
│   ├── captcha.py           验证码自动按压
│   ├── checkpoint.py        流程断点与恢复
│   └── login_flow.py        登录流程状态机
├── task/
│   ├── queue.py             优先级队列（内存 + SQLite 持久化）
│   ├── worker.py            Worker 线程
│   └── scheduler.py         TaskManager + APScheduler 定时任务
├── database/
│   ├── models.py            dataclass 模型与状态枚举
│   └── sqlite.py            SQLite 存储层（WAL，线程安全）
├── proxy/
│   ├── provider.py          代理配置解析 + IP 信息查询
│   ├── manager.py           加权选择 + IP 表现追踪 + 惩罚
│   └── resin.py             Resin 外部粘性代理池
├── updater/manager.py       GitHub Releases 版本检查 + 安装包下载
├── browser/kernel.py        Chromium 内核定位（打包/开发两种布局）
├── logger/logger.py         控制台 + 文件轮转 + 内存缓冲 + 可插拔 sink
├── scripts/
│   ├── setup_browser.py     下载指纹内核（版本锁定）
│   ├── prepare_chromium.py  整理双内核到 build/Chromium
│   └── assemble_dist.py     组装打包产物 + 完整性校验
├── tests/
│   ├── test_core.py         51 个单元测试（不需要浏览器）
│   ├── test_updater.py      更新模块单测（版本比较 / Release 解析）
│   ├── test_theme_contrast.py  配色对比度审计（9 个用例）
│   ├── smoke_gui.py         GUI 冒烟（offscreen）
│   └── smoke_ipc.py         IPC 端到端冒烟
├── build.spec               PyInstaller onedir 配置
├── installer.iss            Inno Setup 安装包配置
├── .github/workflows/       GitHub Actions Windows 编译
└── data/  logs/  profiles/  运行时生成（打包后在 %APPDATA%）
```

## 状态机

```
CREATED → BROWSER_STARTED → LOGIN_PAGE → USERNAME_INPUT → PASSWORD_INPUT
        → CHECK_STATUS → [WAIT_VERIFY → VERIFIED] → COMPLETED / FAILED
```

每个阶段写入 `checkpoints` 表，任务中断后可查看到达位置；`tasks.stage` 同步更新。

账号状态：`NEW / PENDING / RUNNING / OK / WAIT_VERIFY / PASSWORD_WRONG / LOCKED / NOT_FOUND / FAILED / SKIPPED`

## 验证码处理

`flow/captcha.py` 从 `D:\out\OutlookRegister` 的按压实现移植重构，保留全部人类化算法：

- 三段式鼠标轨迹（Bezier 加速 → 随机过冲 → 微调修正）
- 按压位置分布：中心 12% / 边缘 18% / 角落 18% / 随机偏移 52%
- 双击 → 松开 → 长按的按压节奏，按住期间圆周微颤
- 「再次按下」按钮用 `locator.click(position=...)`，由 Playwright 处理嵌套 iframe 坐标转换
- click / dblclick 两种模式按历史通过率加权轮换，保留探索空间
- 失败截图落到 `logs/captcha/`

与原实现的区别：去掉了注册流程耦合，IP 惩罚与统计通过回调交给 `ProxyManager`，
本模块只返回 `CaptchaResult`。`flow.captcha_strategy=1` 切换为半自动（暂停等人工按压）。

需要短信/邮件验证码这类无法自动化的验证时，流程进入 `WAIT_VERIFY`，
等待人工在浏览器窗口完成（最长 `flow.wait_verify_timeout` 秒），完成后自动继续。

## 代理配置

### 方式一：Resin 粘性代理池（推荐，按账号身份粘性）

```yaml
resin:
  enabled: true
  url: "http://127.0.0.1:2260/my-token"   # Resin 基础地址 + Token
  platform: "Default"                      # 业务平台名
  identity_mode: "email_prefix"            # 账号标识：email_prefix | email
```

启用后的行为：

- **浏览器（正向代理）**：每个账号的浏览器会话走 `Platform.Account:Token` 认证的正向代理，
  同一账号始终获得同一出口 IP（粘性）。Account 标识默认取邮箱前缀 —— 登录前就稳定存在，
  全生命周期一致；改 `identity_mode: email` 则用完整邮箱。
- **框架自身 API 请求（反向代理）**：如 ipinfo 出口查询，走
  `<resin_url>/Platform/https/ipinfo.io/...` 并携带 `X-Resin-Account` 头，确保查询的就是该账号真实出口。
- 时区/地理位置随该账号出口 IP 自动匹配。
- 租约继承接口已内置（`Resin.inherit_lease`），供未来需要临时身份→稳定身份过湾的场景使用。
- 状态查看：GUI「代理」页（不含 Token）。

### 方式二：普通端口池 / 单端口代理

```yaml
proxy:
  enabled: true
  mode: single        # single 单端口 / pool 端口池（port_start~port_end）
  type: http
  host: "127.0.0.1"
  single_port: 7890
```

端口池模式下框架自动追踪每个端口的成功率：连续失败的 IP 拉黑、成功多的 IP 加权优先。
注意：`resin.enabled=true` 时 resin 优先生效，此段被忽略。

## 配置要点

`config/config.yaml` 全部参数可用环境变量覆盖，格式 `OA_<SECTION>__<KEY>`：

```bash
set OA_BROWSER__HEADLESS=true
set OA_SYSTEM__MAX_WORKERS=5
```

常用项：

- `browser.executable_path` 内核选择：`fingerprint`（默认，自动定位）/ `patchright`（备用）/ 绝对路径；GUI「浏览器」页可一键切换
- `profile.reuse` true 时同账号固定复用一个 profile，保留登录态减少验证
- `proxy.mode` `single` 单端口 / `pool` 端口池（`port_start`~`port_end`）
- `flow.captcha_strategy` 0 全自动 / 1 半自动
- `system.max_workers` 并发线程数（默认 1），每个线程独占一个浏览器实例。
  调高能提速，但内存与 CPU 占用同比上升，且同时打开多个浏览器更容易被
  目标站点识别为异常流量。上限 16
- `update.repo` 发布仓库（默认 `zhonggy/out-exe`）。自建分发渠道时改这里，
  「关于与更新」页的检查与下载都跟着走
- `update.include_prerelease` 是否把预发布版当作新版本（默认 false）

## 版本号

三处永远同源，都来自 git tag：

```
git tag v1.5.0
  → CI "Resolve version" 算出 OA_VERSION=1.5.0
      → assemble_dist.py 写 dist/OutlookAutomation/version.txt
          → config/loader.py 读它 → APP_VERSION → 「关于」页、窗口标题、--version
      → installer.iss 的 AppVersion（控制面板里显示的版本）
```

CI 里有一步 `Verify version file` 会比对 version.txt 与 tag，不一致就断构建。
源码模式跑时没有 version.txt，回退到 `config/loader.py` 的 `_FALLBACK_VERSION`
—— 发新版时这个常量也要跟着改。

只推 main 不会产生新 Release：Release 那一步带 `if: startsWith(github.ref, 'refs/tags/v')`，
普通 push 只会得到 `0.0.0-build<run>` 的构建产物。

## 安全说明

**已不再有任何网络监听。** 桌面版删除了 FastAPI/uvicorn 与 Token 认证，
GUI 与执行进程之间走本机命名管道（Windows `AF_PIPE` / POSIX Unix socket），
作用域限于当前用户会话，不开 TCP 端口。

**账号密码在 SQLite 中是明文存储**（`accounts.password` 为 TEXT）。
保护完全依赖 Windows 用户账户隔离，需要知道的风险边界：

- 数据库在 `%APPDATA%\OutlookAutomation\data\app.db`，同用户下的任意进程都能读
- 把 `%APPDATA%` 放进云盘同步、做磁盘镜像拷贝，等于密码泄露
- 多用户共用机器时不要用同一个 Windows 账户

密码不进入任何输出：`Account.to_dict()` 默认脱敏为 `***`，GUI 所有展示路径都走这个默认值，
日志、异常堆栈、IPC 推送、导出 CSV 均不含密码。DPAPI 加密列入后续计划。

其他：

- `main.py clean --all-profiles` 会删除全部登录态，执行前需二次确认
- 删除正在被执行进程占用的 Profile 会二次警告（会导致对应任务失败）
- 安装包未做代码签名，首次运行会触发 SmartScreen 警告

## 测试

```bash
python -m pytest tests -q                       # 100 passed，不启动浏览器
QT_QPA_PLATFORM=offscreen python tests/smoke_gui.py   # GUI 冒烟
python tests/smoke_ipc.py                       # IPC 端到端冒烟
```

单元测试覆盖：配置加载与环境变量覆盖、**双路径地基（APP_ROOT / DATA_ROOT）**、
SQLite 全部表 CRUD、账号解析与导入、任务优先级队列与恢复、断点记录、
代理加权与拉黑、Profile 复用与清理、验证码位置算法边界、
**Chromium 内核定位（含内核升级后旧路径失效的回退）**、
**argv 分流（冻结/开发两种模式）**、**IPC 编解码（分帧、半包、坏行、中文）**。

```bash
python tests/smoke_feedback.py      # 每个操作按钮都必须有可见反馈
python tests/smoke_proxy_ui.py      # 代理页保存/测试的完整交互
python tests/smoke_list_refresh.py  # 数据变更后列表正确刷新
python tests/smoke_account_check.py # 账号页勾选：筛选 → 全选 → 批量删除
python tests/smoke_update.py        # 更新页：版本比较 + 检查/下载/安装状态机
python tests/smoke_stop_clear.py    # 停止执行后任务列表清空
python tests/smoke_ui_metrics.py    # 行高/字体度量、高 DPI 缩放
python tests/smoke_ui_geometry.py   # 逐控件几何：文字是否放得下
python tests/smoke_ui_polish.py     # 视觉规范：表单对齐、主按钮唯一、样式一致
```

冒烟测试覆盖：

- **smoke_gui** — 9 个页面构造与切换、快照字段完整性、
  后台任务回调不因 GC 丢失（曾导致所有操作静默无提示）
- **smoke_ipc** — 跨进程真链路（命名管道、logger sink 推送、
  含换行日志不破坏分帧、上下线消息）
- **smoke_worker** — 执行进程拉起 → 收 IPC → PID 接管 → 优雅停止
- **smoke_feedback** — 遍历 13 个改状态的操作，断言每个都产生
  弹窗或状态栏反馈。这条是为防回归而存在的：把 tasks.py 的修复回退，
  它会准确报出 10 项无反馈
- **smoke_proxy_ui** — 代理页保存后配置真的落盘（重读文件验证）、
  测试连接有明确成败反馈、按钮状态正确恢复
- **smoke_list_refresh** — 数据变更后列表必须跟着变：过时响应被丢弃、
  导入时残留的筛选/搜索/翻页被重置、越界页自动回第一页。
  同样是防回归测试，回退修复后它报出 6 项失败
- **smoke_account_check** — 账号页勾选链路：勾选按账号名记忆（刷新/翻页不丢）、
  「全选筛选结果」跨页生效、删除作用于勾选项且不误伤其他状态的账号
- **smoke_update** — 关于与更新页。GitHub 响应全部本地桩造（CI 不依赖外网，
  也避开 API 限流），覆盖版本比较边界、四个按钮的状态机、
  半个安装包不能当成可安装、检查/下载失败后不留死结
- **smoke_stop_clear** — 「停止执行」后任务列表必须清空：先停进程再删记录
  （顺序颠倒会被执行进程的周期补拉又写回）、断点级联删除、
  账号状态不被牽连、「清空队列」只删未开始的
- **smoke_ui_metrics** — 行高与 Label 高度不小于字体实测需求，
  含 100%/125%/150%/175%/200% DPI 缩放；并发线程默认值与派发上限。
  把行高改回写死的 28px，它会在 150% 及以上报失败
- **smoke_ui_geometry** — 逐控件对比"实际可用尺寸"与"文字需要的尺寸"，
  覆盖 3 档缩放 × 2 种窗口宽度。关掉 show 后的重算，它在 125% 报 10 处、
  150% 报 16 处
- **smoke_ui_polish** — 视觉规范：同页表单标签等宽（形成垂直线）但页间
  宽度独立、标签宽度随字体伸缩（写死 110px 会裁掉「Profile 目录」）、
  同一工具栏只有一个实心主按钮、focus ring 与圆角边框规范一致

另有 `tests/test_theme_contrast.py`（走 pytest，9 个用例）纯算色值不依赖渲染：
WCAG 对比度、状态色可区分性、样式表无深色残留。把浅色值换回深色会立刻报 6 项失败。

CI 上这十二项都是打包的前置门禁，任一失败不产出安装包。

端到端也已实测：headless 双 Worker 并发跑 4 个任务全部 COMPLETED，
断点按 `BROWSER_STARTED → LOGIN_PAGE → COMPLETED` 落库，浏览器与 profile 正常回收。

## 界面适配

中文 + 高 DPI 是这个界面最容易出问题的组合，三条约束写在代码里：

**不写死像素尺寸。** 中文字体实际占高约为像素字号的 1.35 倍（西文约 1.15），
而 Qt 会按系统 DPI 缩放样式表里的 px 值（125% → 18px，150% → 21px）。
行高、勾选框宽度、输入框宽度都由 `theme.py` 的 `text_height()` /
`fit_input()` / `fit_checkbox()` / `fit_spinbox()` 按运行时字体算。

**show 之后要重算一次。** 控件构造时还没被样式表 polish，`widget.font()`
返回的是 9pt 默认字体，比样式表的 14px 窄 —— 构造期只能估下限。
`MainWindow.showEvent()` 调 `refit_widget_tree()` 用最终字体重算。

**工具栏自动换行。** `QHBoxLayout` 放不下时会压缩可伸缩控件而不换行：
账号页 9 个控件在 1030px 宽下需要 1026px，搜索框被压到 74px，占位符
完全看不见。`views/flow_layout.py` 的 `FlowLayout` 改为折行，
每个控件都拿到完整宽度。

**配色是浅色（冷调灵白 + 科技蓝 `#2563eb`）。** 色值集中在 `theme.py`，
状态色与背景层次都有命名常量，改主题只需动那一处。

主色选 `#2563eb` 而不是更鲜艳的蓝，是因为它得同时胜任两个角色：实心按钮的
底色（白字在其上 5.17:1）与 Outline 按钮的描边兼文字色（在白底上 5.17:1）。
两个方向都要达 AA，再亮一档就做不了文字色。

**卡片阴影用双层边框模拟。** QSS 不支持 `box-shadow`，真阴影要用
`QGraphicsDropShadowEffect`，而仪表盘一页有 8 张卡片，每张挂个图形特效会让
重绘肉眼可见地变慢。改用“比常规边框更浅的外圈 + 纯白内芯”的明度差制造
浮起感，无渲染代价。

**表单标签按页对齐，宽度跟着字体走。** 标签靠右对齐 + 同页统一宽度，输入框
左边缘形成垂直直线。宽度取本页最宽标签的实测值而不写死：「Profile 目录」
在 100% 就需 140px，200% 下需 179px，写死 110px 会直接裁字。对齐还必须是
“页内”语义：代理页最长标签只 66px，关于页是 178px，全局拉平会让代理页
白白浪费一大截横向空间。

浅色比深色难调的地方：深色下"提亮"总能拉开对比，浅色下压暗过头会发脏、
不够又发飘。深色主题用的亮绿 `#3fb950` 在白底上对比度只有 2.3:1，
远低于 WCAG AA 的 4.5:1，所以状态色全部重挑过。

另一个坑是表格有三种底色（白行 / 斑马行 / 选中行），只在白底上验对比度
会漏——实测选中行底色会让状态色掉到 4.0:1。`tests/test_theme_contrast.py`
按三种底色分别验，并检查状态色两两可区分、不与交互主色撞色
（撞色会让"运行中"文字看起来像可点的链接）。

## 派发数量

单次派发上限 **5000**，是体验上的软限制而非技术瓶颈：

- 派发速度实测约 440 条/秒，12000 条约 27 秒。数据库与队列都能承受
- `queue.restore()` 一次最多拉 10000 条进内存队列，但执行进程每 2 秒
  补拉一次，超出部分照样会被处理 —— 不丢任务，只是分批进入
- 设上限是为了避免"点一下等半分钟"，以及一次把上万账号锁成 PENDING
  后想反悔只能手工重置

派发对话框会显示待处理数量、本次派发数量与预估耗时；超过 800 条时
按钮上有实时进度。需要更多请分批派发。

## 打包

正式编译在 GitHub Actions 上完成，本地不作为编译环境。

```
git push                    → 跑测试 + 打包，产物在 Artifacts
git tag v1.0.0 && git push --tags  → 额外发布 GitHub Release
```

本地想手动打一次：

```bash
python scripts/prepare_chromium.py      # 整理双内核到 build/Chromium
pyinstaller --noconfirm build.spec      # onedir 产物到 dist/
python scripts/assemble_dist.py         # 拷内核 + 校验完整性
iscc installer.iss                      # 生成安装包
```

**必须用 onedir，不能用 onefile**：`patchright/driver/` 含 node.exe 与完整 node 包
（约 96MB），onefile 下每次启动都要解压；双进程模型下两个进程各解压一份，
磁盘与启动时间都翻倍，且杀软对自解压行为误报率高。

实测体积（v1.0.0）：安装包 **289MB**。主要构成是两个 Chromium 内核
（指纹 425MB + 备用 331MB）、PySide6 运行时、patchright driver（96MB，含 node.exe）。
已剔除 `chromium_headless_shell`（197MB）与 `ffmpeg` —— 本项目 headless=false
且不录屏，用不到。

两个内核缺任一个就构建失败（而不是警告）：选双内核就是为了指纹内核失效时
能切备用，静默少一个等于发了个没退路的包。

## 扩展新流程

继承 `BaseFlow` 并注册，`Task.type` 与 `name` 对应即可被 Worker 调度：

```python
from flow import BaseFlow, FlowResult, register_flow
from database import FlowStage

@register_flow
class MyFlow(BaseFlow):
    name = "myflow"

    def run(self, account="", password="", **kwargs):
        self.mark(FlowStage.BROWSER_STARTED)
        self.page.goto("https://example.com")
        return FlowResult(True, FlowStage.COMPLETED.value)
```

```bash
python main.py run --type myflow --limit 5
```

## 后续

- 密码 DPAPI 加密（绑当前 Windows 用户，在 `database/sqlite.py` 透明转换）
- 代码签名（消除 SmartScreen 警告）
- 真正的任务暂停（需给 `task/worker.py` 加 `pause_event`，现在只有开始/停止）
- 插件系统：`flow` 注册表已具备基础，下一步把流程包装为
  `plugins/<name>/{plugin.py,config.yaml}`，支持目录扫描自动加载
