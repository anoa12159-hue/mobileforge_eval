# Check Tool — MobileForge 数据审核

可视化审核 `metadata.json` 和 `test_cases.json`，对照截图检查页面结构、导航关系、测试用例的正确性。

## 操作流程

### 第一轮：页面类型与 Tab 结构校正

1. 打开「页面审核」Tab，对照截图逐页检查 `page_type` 和 `tab_position`
2. 将被误标为 `main_page` 的页面改为正确的子类型（`sub_page` / `overlay` / `sub_tab`）
3. 清除非主页面的 `tab_position`，确保只有真正的底部 Tab 主页面保留 `tab_position`
4. 若某个截图不属于该 App 或无效，点击截图右上角 `×` 删除（软删除，可恢复）
5. 点击「保存审核」— 系统自动根据 `main_page` + `tab_position` 重建 `tab_structure`，并重新生成测试用例

### 第二轮：父子关系与导航关系校正

1. 切换到「导航关系」Tab，检查 `tab_structure` 底部导航栏是否正确
2. 逐条检查 `page_relationships`，确认每对父子关系的 `parent`、`page`、`trigger` 描述准确
3. 若有同 `tab_position` 的未关联页面（橙色虚线卡片），点击「+ 添加关系」建立 `sub_tab` 关系
4. 回到「页面审核」Tab，检查子页面的 `parent_page` 和 `trigger` 是否与导航关系一致
5. 点击「保存审核」

### 第三轮：测试用例复核

1. 切换到「测试用例」Tab，依次展开 `page_exists`、`tab_navigation`、`parent_child_navigation`
2. 重点检查 `parent_child_navigation`：对照截图确认 `from_page` → `to_page` 的导航路径合理、`trigger` 描述能让 VLM 准确定位操作元素
3. 标记每条用例为「通过」或「有问题」，有问题的添加备注说明
4. 点击「保存审核」

> **提示**：保存时若 metadata 有变更，系统会自动调用 `testgen` 重新生成测试用例，无需手动操作。

## 功能

- **页面审核**：逐页展示截图 + metadata 字段（page_type、tab_position、visual_anchors 等），支持内联编辑；按 tab_position 排序，子页面缩进显示在父页面下方
- **导航关系**：可视化 tab_structure（支持增删改）和 page_relationships（按 tab_position 排序，附带缩略截图）；自动检测同 tab_position 未关联页面
- **测试用例**：按 type 分组折叠展示（page_exists / tab_navigation / parent_child_navigation），按 tab_position 排序，显示 from→to 截图
- **自动检测**：后端预检 tab_position 不匹配、截图文件缺失、page_name 引用不一致等问题
- **标记与备注**：每个页面/用例可标记「通过 / 有问题 / 跳过」，并添加文字备注
- **页面删除**：支持截图软删除（重命名为 `.png.deleted`），自动级联清理关联关系、tab_structure 引用和子页面引用
- **安全导出**：修正后的 JSON 保存为 `metadata.reviewed.json` / `test_cases.reviewed.json`，不覆盖原文件；保存时自动重建 tab_structure 并按需重新生成测试用例

## 快速启动

```bash
cd annotator/check-tool
pip install -r requirements.txt
python server.py --input-dir ../../input/mobileforge
```

浏览器打开 http://localhost:8767

## CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--input-dir` / `-i` | `../../input/mobileforge` | mobileforge 数据目录 |
| `--port` / `-p` | `8767` | 服务端口 |
| `--host` | `0.0.0.0` | 监听地址 |

## 输出文件

保存审核后，在每个 app 目录下生成：

- `metadata.reviewed.json` — 修正后的 metadata（含 `reviewed_at` 时间戳）
- `test_cases.reviewed.json` — 修正后的测试用例
- `review_state.json` — 审核状态（每个页面/用例的 pass/fail/skip + 备注）


## 备注

1\ 检查所有main_page，确保每个tab只有1个（main_page的数量 <= tab数量）
2\ 如果有页面的type从main_page改为其他页面，则需要给它加上parent_page和trigger
3\ 确保所有跟随在main_page下面的页面的parent_page都是该main_page，注意是否有和main_page重名情况
4\ 在测试用例页，逐个检查父子关系的trigger（比页面审核检查更方便）