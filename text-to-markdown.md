# OutlookAutomation 本地自动化框架项目规划

## 1\. 项目定位

OutlookAutomation 是一个基于 Python + Patchright 的本地自动化框架。

项目目标：

*   Windows 本地电脑运行
    
*   不使用 Docker
    
*   模块化设计
    
*   吸收 OutlookRegister 的优秀架构设计
    
*   实现浏览器管理、配置管理、任务管理、账号状态管理
    
*   支持后续扩展不同自动化流程
    

* * *

# 2\. 技术架构

## 开发环境

*   Windows 10/11
    
*   Python 3.11+
    
*   Patchright
    
*   Chromium
    
*   SQLite
    
*   YAML 配置系统
    

## 可选组件

*   FastAPI（本地 API）
    
*   Vue（本地管理界面）
    
*   APScheduler（任务调度）
    

* * *

# 3\. 项目目录结构

```

OutlookAutomation/

├── main.py

├── browser/
│   ├── browser.py
│   ├── context.py
│   ├── profile.py
│   └── manager.py

├── config/
│   ├── config.yaml
│   └── loader.py

├── account/
│   ├── models.py
│   ├── manager.py
│   └── status.py

├── flow/
│   ├── login_flow.py
│   ├── detector.py
│   ├── action.py
│   └── checkpoint.py

├── task/
│   ├── scheduler.py
│   ├── worker.py
│   └── queue.py

├── database/
│   ├── sqlite.py
│   └── models.py

├── proxy/
│   ├── manager.py
│   └── provider.py

├── logger/
│   └── logger.py

├── api/
│   └── server.py

├── data/
├── profiles/
├── logs/

├── requirements.txt
└── README.md
```

* * *

# 4\. 吸收 OutlookRegister 的架构优势

## Browser 模块

参考 OutlookRegister 的设计：

保留：

*   Patchright 初始化
    
*   Chromium 启动管理
    
*   Browser 生命周期管理
    
*   Context 创建
    
*   页面控制
    

功能：

```

启动浏览器

↓

创建浏览器环境

↓

执行任务

↓

保存状态

↓

关闭浏览器
```

* * *

## Config 配置系统

使用 YAML：

示例：

```yaml

browser:
  headless: false
  timeout: 60000

system:
  max_workers: 3

database:
  path: data/app.db
```

管理：

*   浏览器参数
    
*   任务参数
    
*   数据路径
    
*   日志设置
    

* * *

# 5\. 开发路线

# v1.0 基础框架

目标：

建立自动化核心。

功能：

## Browser Engine

实现：

*   Patchright 浏览器封装
    
*   Chromium 管理
    
*   浏览器实例管理
    
*   Profile 管理
    

## Config System

实现：

*   YAML配置读取
    
*   参数管理
    

## Database

使用 SQLite：

保存：

*   账号信息
    
*   任务记录
    
*   执行状态
    

## Logger

实现：

*   控制台日志
    
*   文件日志
    
*   错误记录
    

* * *

# v1.1 任务管理系统

目标：

实现任务生命周期管理。

新增：

```

task/
```

功能：

*   创建任务
    
*   任务队列
    
*   Worker执行
    
*   状态更新
    
*   错误恢复
    

流程：

```

任务创建

↓

Task Queue

↓

Worker

↓

Browser Engine

↓

结果保存
```

* * *

# v1.2 本地管理面板

目标：

增加可视化操作。

技术：

```

FastAPI
+
Vue
```

访问：

```

http://127.0.0.1:8000
```

功能：

## 任务管理

*   查看任务
    
*   创建任务
    
*   停止任务
    
*   查看运行状态
    

## 浏览器管理

*   查看浏览器实例
    
*   查看Profile
    

## 日志管理

*   实时日志
    
*   错误记录
    

* * *

# v1.3 代理与环境管理

目标：

增强运行环境管理。

## Proxy Manager

支持：

*   HTTP代理
    
*   SOCKS代理
    
*   代理配置管理
    

## Profile Manager

管理：

```

profiles/

├── profile001
├── profile002
└── profile003
```

每个环境独立：

*   浏览器缓存
    
*   Cookie
    
*   Local Storage
    

* * *

# 6\. 自动化流程设计

## 登录流程模块

流程：

```

任务开始

↓

启动浏览器

↓

打开登录页面

↓

读取账号列表

↓

选择账号

↓

输入账号

↓

点击下一步

↓

进入密码页面

↓

选择密码登录

↓

输入对应密码

↓

点击下一步

↓

检测账户状态

↓

处理页面状态

↓

完成流程
```

* * *

# 7\. 账号管理

账号来源：

```

accounts.txt
```

格式：

```

账号----密码
```

例如：

```

account1@example.com----password1
account2@example.com----password2
```

Account Manager 负责：

*   读取账号
    
*   分配任务
    
*   保存状态
    
*   记录结果
    

* * *

# 8\. Flow Engine 流程引擎

目录：

```

flow/

├── login_flow.py
├── detector.py
├── action.py
└── checkpoint.py
```

功能：

## Action

负责：

*   页面操作
    
*   输入
    
*   点击
    

## Detector

负责：

*   页面状态检测
    
*   判断当前流程
    

## Checkpoint

负责：

*   保存流程节点
    
*   中断恢复
    

* * *

# 9\. 验证处理设计

当流程遇到账户安全验证或人工确认页面时：

系统进入：

```

WAIT_VERIFY
```

处理：
*  从D:\out\OutlookRegister里的自动按压过验证的功能复制过来
    
*   检测验证完成
    
*   继续后续流程
    

流程：

```

发现验证

↓

开始按压

↓

验证完成

↓

恢复任务

↓

记录结果
```

* * *

# 10\. 任务状态设计

```

CREATED

↓

BROWSER_STARTED

↓

LOGIN_PAGE

↓

USERNAME_INPUT

↓

PASSWORD_INPUT

↓

CHECK_STATUS

↓

WAIT_VERIFY

↓

VERIFIED

↓

COMPLETED
```

* * *

# 11\. 数据库设计

SQLite：

保存：

## Account

字段：

*   id
    
*   account
    
*   password
    
*   status
    
*   last\_run
    

## Task

字段：

*   id
    
*   type
    
*   status
    
*   start\_time
    
*   end\_time
    

## Browser Profile

字段：

*   profile\_id
    
*   path
    
*   status
    

* * *

# 12\. 后续扩展

## v1.4 插件系统

目标：

支持不同自动化流程。

结构：

```

plugins/

├── plugin_a

├── plugin_b

└── plugin_c
```

每个插件：

```

plugin.py

config.yaml

README.md
```

* * *

# 13\. 最终目标

打造一个：

> 本地运行的自动化框架平台

核心能力：

*   浏览器自动化
    
*   配置管理
    
*   账号管理
    
*   任务调度
    
*   状态记录
    
*   日志分析
    
*   可视化管理
    
*   插件扩展
    

项目定位：

```

OutlookRegister
        |
        |
吸收架构设计
        |
        ↓

OutlookAutomation Framework
```

不是复制原项目，而是建立一个更加通用的本地自动化平台。