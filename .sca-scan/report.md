# SCA 扫描报告 — AI_Fund_Assistant

> 工具：ai-sca-scan v1.0.0（零安装，纯公开 API）｜生成时间：2026-11-20｜扫描对象：`backend/`（FastAPI，requirements.txt 混合约束）+ `frontend/`（React/Vite，package-lock.json 全量树）+ deps.dev 传递图谱估计
> 数据输出：本目录 `components.json` / `findings.json` / `sbom.cdx.json` / `sarif.json` / `summary.json`

## 1. API 状态矩阵

| 通道 | 状态 | 用量 | 说明 |
|---|---|---|---|
| Registry (npm/PyPI) | ✅ | 直接依赖核验 | 存在性/下载量；0 幻觉 |
| OSV querybatch + 详情 | ✅ | 442 查询分批 + 全量详情 | 主漏洞通道 |
| deps.dev | ✅ | 许可证 + 传递图谱（floor 种子） | 无锁文件的 Python 传递闭包估计 |
| GHSA | ⚠️ 限速 | 403（本机当日配额耗尽） | OSV 已镜像 GHSA 记录，完整性不受影响 |
| CISA KEV | ✅ | 1786 条（缓存） | **1 命中 → P0** |
| EPSS | ✅ | 全量 CVE | 利用概率 |
| NVD | ✅ | 缺失 CVSS 补全（≤10 条，6.5s 间隔） | vec-only 记录补分 |

**降级标注：`ghsa: rate_limited`**（建议配置 GITHUB_TOKEN）。

## 2. 扫描摘要

| 维度 | 数值 |
|---|---|
| 组件总数 | **443（442 解析，99.8%）**：npm 锁文件树 421+ / Python 直接 23（含传递估计 30+） |
| 幻觉包 / 弃用包 | 0 / 0 |
| dev-only 组件 | 148（前端构建链 vite/eslint 等全部正确降档） |
| 漏洞候选 | **69**：P0 **15** / P1 19 / P2 25 / P3 10 |
| 恶意包 | 0 |
| KEV 命中 | **1（starlette，P0）** |
| 许可证来源 | deps.dev |

**最重要的单条发现**：`starlette@0.37.2`（fastapi==0.111.0 的传递依赖，无锁文件下由 deps.dev 图谱估计补出）命中 **CISA KEV**（CVE-2026-48710，Host 头投毒，EPSS **0.70**）。这正是贵团队在 SecurityCopilot 项目中亲手处理过的同一漏洞类（GHSA-86qp-5c8j-p5mr，当时评级"本项目可被利用"）——**AI_Fund_Assistant 的 fastapi 0.111.0 仍处于该风险面**。

## 3. 组件清单（关键组件）

| 组件 | 版本 | 属性 | 许可证 | 备注 |
|---|---|---|---|---|
| fastapi | 0.111.0 | direct | MIT | **传递引入 starlette 0.37.2（KEV）** |
| starlette | 0.37.2 | transitive（图谱估计） | BSD-3-Clause | 7 条 advisory，1 条 KEV |
| axios | 1.16.1 | direct (前端) | MIT | **21 条 advisory（P0×12）** |
| echarts | 5.6.0 | direct (前端) | Apache-2.0 | XSS 1 条（P0） |
| react-router-dom | 6.30.3 | direct (前端) | MIT | 开放重定向→XSS（P0） |
| cryptography | 42.0.0 | direct（约束下限近似） | Apache-2.0/BSD | 约束 ≥42.0,<46.0；**9 条 advisory** |
| python-dotenv | 1.0.1 | direct | BSD-3-Clause | 1 条（精化后降级） |
| requests | 2.34.2 | direct（图谱填充） | Apache-2.0 | 约束 ≥2.32.4,<3.0；0 命中 ✅ |
| akshare | 1.18.63 | direct（图谱填充） | MIT | 与贵注释"本地实测版本"一致 ✅ |
| chinese-calendar | 1.11.0 | direct（图谱填充） | MIT | 与贵注释一致 ✅ |
| numpy | 未解析 | direct | — | 约束 ~=2.0.1，图谱填充 2.5.4 违反上界被拒绝（见 §7） |
| openai | 1.35.0 | direct | Apache-2.0 | llm/openai_provider 等使用 |
| vite / eslint / typescript 等 | 锁定 | dev | — | 148 个 dev 组件全部降档 |

许可证全部宽松（MIT/Apache-2.0/BSD），无 strong copyleft。

## 4. 漏洞发现（候选，按优先级降序）

### P0（15 条）

| 漏洞 | 组件 | EPSS | 修复版本 | LLM 静态判断 |
|---|---|---|---|---|
| **CVE-2026-48710** | **starlette@0.37.2** | **0.703** | 1.0.1 | ⛔ **KEV（2026-09-02 收录，不可下调）**。Host 头投毒 `request.url.path`；backend/main.py 未检出应用层 url.path 依赖，但框架级风险 + 已知被利用 + EPSS 0.70。**修复路径已被贵团队在 SecurityCopilot 实证**：fastapi 0.162.0 → starlette 1.10.0（0 条 advisory） |
| CVE-2026-101898 | axios@1.16.1 | 0.005 | 1.20.0 | HTTP/2 适配器绕过配置的 DNS/代理 → 前端直连类 SSRF；`src` 中 axios 引用多处，前端为攻击面入口 |
| CVE-2026-101900 | axios@1.16.1 | 0.005 | 1.20.0 | Fetch 适配器继承头注入 |
| CVE-2026-67319 | axios@1.16.1 | 0.003 | 1.18.0 | 嵌套 option 对象原型污染链 |
| CVE-2026-101902 | axios@1.16.1 | 0.004 | 1.20.0 | 默认适配器原型污染 gadget |
| CVE-2026-67315 | axios@1.16.1 | 0.005 | 1.18.0 | NO_PROXY 0.0.0.0 绕过 |
| CVE-2026-67320 | axios@1.16.1 | 0.005 | 1.18.0 | Node 适配器继承认证头 |
| CVE-2026-101904 | axios@1.16.1 | 0.005 | 1.20.0 | 继承 headers 头注入 |
| CVE-2026-101905 | axios@1.16.1 | 0.004 | 1.20.0 | Node 适配器原型污染 |
| CVE-2026-67316 | axios@1.16.1 | 0.004 | 1.18.0 | 原型污染 gadget 可改写请求参数 |
| CVE-2026-101908 | axios@1.16.1 | 0.005 | 1.20.0 | fetch 适配器原型污染 |
| CVE-2026-101909 | axios@1.16.1 | 0.004 | 1.20.0 | axios.toFormData 原型污染 |
| CVE-2026-67314 | axios@1.16.1 | 0.004 | 1.18.0 | auth 子字段原型污染 |
| CVE-2026-45249 | echarts@5.6.0 | 0.007 | 6.1.0 | XSS；前端渲染 AI 生成的分析内容（react-markdown 场景）时若 chart 配置含不可信串则有真实注入面 |
| CVE-2026-53668 | react-router-dom@6.30.3 | 0.003 | 6.30.6 | 开放重定向→XSS |

> axios 21 条中 9 条 DoS/ReDoS 类为 P1（见下）；**升级 axios ≥1.20.0 一次覆盖全部 21 条**（十条 fix 1.18.0 与十一条 fix 1.20.0 的并集；已回验 axios@1.20.0 在 OSV 为 0 条）。

### P1（19 条）

| 组 | 漏洞 | 修复版本 | 判断 |
|---|---|---|---|
| axios ×9 | CVE-2026-67312/67313/67317/67318/67321、CVE-2026-101899/101901/101903/101906 | 1.18.0 / 1.20.0 | DoS/ReDoS/注入辅助类；随 axios ≥1.20.0 一并消除 |
| **cryptography@42.0.0 ×9** | 见下方拆解 | — | ⚠️ **版本为约束下限近似（实际安装版本在 [42.0, 46.0) 内未知），置信 low** |
| python-dotenv@1.0.1 ×1 | CVE-2026-28684（符号链接 set_key 任意写） | 1.2.2 | **LLM 精化：P1→P2（实际不可达）**：全仓仅 `load_dotenv`（backend/config.py:11），`set_key` 零调用 |

**cryptography 版本不确定度拆解**（约束 `>=42.0,<46.0`，扫描按下限 42.0.0 匹配）：

- **范围内无修复、与实际安装版本无关的 5 条（确定的真候选）**：GHSA-537c-gmf6-5ccf（fix 48.0.1）、CVE-2026-69249（49.0.0）、CVE-2026-69248（49.0.0）、CVE-2026-34073（46.0.6）、CVE-2026-26007（46.0.5）——修复版本全部 ≥46，**允许范围（<46.0）内无任何补丁**，抬上限才能消除；
- **依赖实际版本的 4 条（可能高估）**：CVE-2024-0727（fix 42.0.2）、CVE-2024-26130（42.0.4）、GHSA-h4gh（43.0.1）、CVE-2024-12797（44.0.1）——若实际安装 ≥44.0.1 则这四条已消除。**建议：在 backend 运行环境执行 `pip freeze | grep cryptography` 确认实际版本，再决定处置**。

### P2（25 条）/ P3（10 条）

分布：starlette 0.37.2 其余 6 条（multipart DoS、UNC SSRF、方法分派等，随 fastapi 升级一并消除）、vite 构建链 dev 组件若干（P2 dev 降档）、follow-redirects/react-router 传递 P3 等。完整清单见 `findings.json`。

## 5. AI 幻觉与投毒风险

**无**。前端 npm 直接依赖 + 后端 Python 直接依赖全部通过注册表核验；未检出 slopsquatting（与知名包相似但低下载量的可疑名 0 个）。当前无 deprecated 组件。

## 6. 许可证与兼容性

全部宽松许可（MIT/Apache-2.0/BSD）。项目已带 LICENSE 文件。片段级许可证检测：unavailable（AI 生成代码嵌入片段的许可风险不覆盖，见 §8）。

## 7. 未解析组件

| 组件 | 约束 | 原因 |
|---|---|---|
| numpy | `~=2.0.1` | deps.dev 图谱给出的版本 2.5.4 **违反约束上界（<2.1.0），已拒绝填充**（贵注释确认本地实测 2.0.2）。numpy 2.0.x 当前无已知 CVE 命中，但该组件处于无漏洞比对状态 |

**根治建议**：在 backend 实际运行环境生成锁文件（`pip freeze > requirements.lock`），将 `~=`/`>=` 约束全部钉死——SecurityCopilot 项目已经这样做了，可直接复制该实践。

## 8. 能力边界声明（原样输出）

> 本次扫描覆盖了：锁文件驱动的组件解析（npm/PyPI/Go/Cargo/Composer/RubyGems/Maven/NuGet）、注册表存在性核验（幻觉包/弃用/下载量/slopsquatting 启发式）、OSV 批量漏洞匹配（含恶意包记录）、deps.dev 包级许可证、GHSA 交叉验证（速率允许范围内）、CISA KEV 与 EPSS 利用性优先级、NVD CVSS/CWE 补全（可选）、静态包名引用检索作为可达性近似。
>
> 本次扫描**未**覆盖：片段级许可证检测（需 SCANOSS snippet API）、真正的函数级调用图可达性分析（static_signal 是包名引用近似，非数据流分析）、容器镜像与 OS 包扫描、语言运行时自身版本 CVE、私有 registry 组件、无锁文件场景的高置信版本确定。
>
> **输出为候选，不是判定。** preliminary_priority 是分诊起点，不是处置依据；升级/降级依据见各条 reason。

## 9. 总评与处置优先级

| 优先级 | 处置 | 动作 |
|---|---|---|
| 1 | **fastapi 0.111.0 → ≥0.162.0** | 消除 KEV starlette CVE-2026-48710 + 其余 6 条 starlette advisory（SecurityCopilot 已验证该组合） |
| 2 | **axios 1.16.1 → ≥1.20.0** | 一次消除前端 21 条（12 P0 + 9 P1） |
| 3 | echarts 5.6.0 → ≥6.1.0 / react-router-dom → ≥6.30.6 | 各 1 条 XSS；echarts 为 major 升级，需回归图表用例 |
| 4 | cryptography：先 `pip freeze` 确认实际版本 → 抬上限 ≥48/49 并回归 COSY 信封用例 | 范围内无修复的 5 条只有升级上限可解 |
| 5 | python-dotenv ≥1.2.2、pyasn1（若引入）等补丁级 | 低风险随批处理 |
| 6 | 生成 `requirements.lock` | 根治 numpy 未解析与版本漂移问题 |
