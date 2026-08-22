# OutlookAutomation

基于 Python + Patchright 的本地自动化框架。Windows 本地运行，不依赖 Docker。

已实现 v1.0 → v1.3：浏览器管理、配置系统、账号管理、流程引擎（含验证码自动按压）、
任务队列与 Worker 池、本地管理面板、代理与环境（Profile）管理。

## 快速开始

```bash
pip install -r requirements.txt
patchright install chromium
python scripts/setup_browser.py    # 下载 fingerprint-chromium 指纹内核（约 180MB，自动写入配置）

python main.py doctor              # 环境自检
```

> 指纹内核（fingerprint-chromium）体积大不入仓库。`setup_browser.py` 会从官方 Releases
> 自动下载、解压到 `browsers/fingerprint-chromium/` 并写好配置；国内网络会自动探测本机
> 代理端口（7897/7890/10809）。也可以手动从
> [Releases](https://github.com/adryfish/fingerprint-chromium/releases) 下载 Windows x64 zip
> 解压后把 chrome.exe 路径填到 config.yaml 的 `browser.executable_path`。
> 不想用指纹内核则清空 `executable_path` 并设 `fingerprint_enabled: false`。

创建 `accounts.txt`（每行一条，默认分隔符 `----`）：

```
account1@example.com----password1
account2@example.com----password2
```

然后：

```bash
python main.py import              # 导入账号到 SQLite
python main.py run --limit 10      # 跑 10 个登录任务
python main.py serve               # 启动管理面板 http://127.0.0.1:8000
```

首次 `serve` 会在 `data/api_token` 生成 Token，并在启动日志中打印。面板首次打开需粘贴该 Token。

## 命令行

| 命令 | 说明 |
| --- | --- |
| `python main.py serve` | 启动本地面板（`--port` / `--host` / `--no-auth`） |
| `python main.py import` | 导入 `accounts.txt`（`--file` 指定其他文件） |
| `python main.py run` | 执行任务（`--limit` / `--account` / `--type` / `--workers`） |
| `python main.py stats` | 账号 / 任务 / 验证码 / 代理 / 环境统计 |
| `python main.py accounts` | 列出账号（`--status NEW`） |
| `python main.py tasks` | 列出任务记录 |
| `python main.py export` | 导出结果 CSV（不含密码） |
| `python main.py clean` | 清理（`--profiles` / `--tasks` / `--all-profiles`） |
| `python main.py doctor` | 依赖与运行环境自检 |

## 目录结构

```
OutlookAutomation/
├── main.py                  CLI 入口
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
│   └── manager.py           加权选择 + IP 表现追踪 + 惩罚
├── api/
│   ├── server.py            FastAPI 接口（Token 认证）
│   └── static/index.html    Vue3 CDN 单文件面板
├── logger/logger.py         控制台 + 文件轮转 + 内存缓冲
├── tests/test_core.py       26 个单元测试（不需要浏览器）
├── data/  logs/  profiles/  运行时生成
└── config.yaml 之外无需其他配置
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
- 状态查看：面板「浏览器/代理」页或 `GET /api/proxy` 的 `resin` 字段（不含 Token）。

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

- `browser.executable_path` 留空用 patchright 自带 Chromium；指向 fingerprint-chromium 时可配合 `fingerprint_enabled`
- `profile.reuse` true 时同账号固定复用一个 profile，保留登录态减少验证
- `proxy.mode` `single` 单端口 / `pool` 端口池（`port_start`~`port_end`）
- `flow.captcha_strategy` 0 全自动 / 1 半自动
- `system.max_workers` 并发数，每个 Worker 独立浏览器实例

## 安全说明

- API 默认仅监听 `127.0.0.1` 并开启 Token 认证。该接口能启动浏览器、读写账号数据，
  不要改成 `0.0.0.0`，也不建议用 `--no-auth`：关闭认证后同机任意进程都能下发任务。
- Token 存放在 `data/api_token`；`/api/config` 返回时已脱敏，账号列表接口不返回明文密码。
- 导出 CSV 不含密码字段。
- `main.py clean --all-profiles` 会删除全部登录态，执行前需二次确认。

## 测试

```bash
python -m pytest tests -q      # 26 passed，不启动浏览器
```

已验证：配置加载与环境变量覆盖、SQLite 全部表 CRUD、账号解析与导入、任务优先级队列与恢复、
断点记录、代理加权与拉黑、Profile 复用与清理、验证码位置算法边界、API 认证与全部只读端点。

端到端也已实测：headless 双 Worker 并发跑 4 个任务全部 COMPLETED，
断点按 `BROWSER_STARTED → LOGIN_PAGE → COMPLETED` 落库，浏览器与 profile 正常回收。

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

## 后续（v1.4 插件系统）

当前 `flow` 注册表已具备插件基础。下一步是把流程包装为 `plugins/<name>/{plugin.py,config.yaml}`
形式，支持目录扫描自动加载，无需改动框架代码。
