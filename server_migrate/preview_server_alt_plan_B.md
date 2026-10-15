# 备选方案 B：用一次性「全局 vite」让 navigation_test 不依赖 per-project node_modules

> 这是一个**备选方案**。AgenticForge 默认的方案是「方案 A」——`testgen/runner.py`
> 在检测不到 `node_modules/.bin/vite` 时会自动用 Python 内置 SPA 静态服务器
> 兜底（详见 `testgen/runner.py` 中的 `_start_preview_server`）。
>
> 只有以下情况你才需要看这份文档：
>
> - 方案 A 的 Python 服务器在你环境里有任何不兼容（极少发生，因为 stdlib `http.server` 是纯标准库）；
> - 你希望 navigation_test 的服务端口完全由 `vite preview` 提供（与论文实验完全同进程类型）；
> - 你需要 vite 命令做调试（手动跑 `vite preview` 看页面）。

---

## 工作原理

`testgen/runner.py` 调用 `npx vite preview --port N --strictPort`。`npx` 的查找顺序：

1. `<项目根>/node_modules/.bin/vite` ← 默认会优先用
2. `$PATH` 里的 `vite` ← **这是本方案做的事**
3. 联网下载到 npm 缓存（慢、不可控）

只要全局装一个 `vite`，第 2 步就命中，**无须每个样本恢复 ~89 MB 的 node_modules**（216 项目 ≈ 19 GB）。

## 坑：项目自带的 `vite.config.ts` 会拖累

每个样本的 `vite.config.ts` 长这样：

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'   // ← 这个 plugin 不在全局
export default defineConfig({ plugins: [react()] })
```

`vite preview` 默认会读取项目的 `vite.config.ts`。光装 `vite` 全局、不装 `@vitejs/plugin-react`，
config 加载阶段就会 `Error: Cannot find package '@vitejs/plugin-react'` 退出。

**解决办法：** 用 `--config` 指向一个**空的、不 import 任何 plugin** 的配置。
`vite preview` 实际只需要默认值（`outDir=dist`、SPA fallback 内置），plugins 在 preview 阶段完全不执行——
所以扔掉项目自己的 config 没有任何副作用。

## 落地步骤

### 1. 新服务器一次性准备（无需 sudo，nvm 用户态目录里）

```bash
source env.sh              # 确保已 nvm use 20
npm i -g vite@^6           # ~30 MB，一次

# 写一个空 config 供所有项目共用
mkdir -p ~/.cache/designforge
cat > ~/.cache/designforge/vite-empty.config.mjs <<'EOF'
export default {}
EOF

# 自检
which vite                                  # 应输出 ~/.nvm/versions/node/v20.x/bin/vite
vite --version                              # 应输出 vite/6.x.x ...
```

### 2. 修改 `testgen/runner.py`（如果你确定要切换到此方案）

> ⚠️ 方案 A 已经在 `_start_preview_server` 里实现了「无 node_modules → Python 后端」的自动兜底。
> 走方案 B 等价于把这段子函数里的 Python 分支换成「调全局 vite」。

把 `_start_preview_server` 的 Python 分支改成：

```python
def _start_global_vite_preview(run_dir: Path, port: int) -> _ViteSubprocessPreview:
    empty_cfg = Path("~/.cache/designforge/vite-empty.config.mjs").expanduser()
    proc = subprocess.Popen(
        ["npx", "vite", "preview",
         "--config", str(empty_cfg),
         "--port", str(port), "--strictPort"],
        cwd=str(run_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return _ViteSubprocessPreview(proc)
```

并把 `_start_preview_server` 中 `_start_python_spa_preview` 的调用替换为 `_start_global_vite_preview`。

或者更优雅地：增加一个 `TESTGEN_SERVER=global_vite` 取值，让两个方案可并存切换。

### 3. 资源对比

| 方案 | npm 安装 | 每项目 node_modules | 总磁盘 | 代码改动 | 论文复现一致性 |
|---|---|---|---|---|---|
| 全量恢复 `restore_node_modules.sh` | 每个项目独立装 | 89 MB × 216 | **~19 GB** | 0 | 100% |
| **方案 A（默认）：Python SPA fallback** | 0 | 0 | **0 额外** | 已实装 | 99%（响应字节级与 vite preview 一致，已 parity 测试） |
| **方案 B：全局 vite** | `npm i -g vite` 一次 | 0 | **~30 MB** | ~15 行 | 100%（与论文实验完全同一个 vite 进程类型） |

## 何时选 B 而非 A

- ✅ 严格要求 navigation_test 进程跟论文实验**类型完全一致**（都是 `vite preview` 子进程，便于审稿人复现）；
- ✅ 你已经习惯 `npm`，不介意一次性全局安装；
- ❌ 否则不必费这个事——方案 A 已经在响应字节、状态码、Content-Type 上和 vite preview 一致。

## 排错

| 现象 | 检查 |
|---|---|
| `Error: Cannot find package '@vitejs/plugin-react'` | 没传 `--config <空 config>`；按上面步骤 2 改 testgen/runner.py |
| `command not found: vite` | `nvm use 20` 没生效；重新 `source env.sh` |
| 全局 vite 装完仍走不通 | `which vite` 应在 `~/.nvm/versions/node/v20.x/bin/`；若不是说明 npm prefix 不对，跑 `npm config get prefix` 看一下 |
| 端口冲突 | 设 `TESTGEN_PREVIEW_PORT=18099` 强制端口 |
