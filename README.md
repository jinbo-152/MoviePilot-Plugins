# MoviePilot-Plugins

个人维护的 [MoviePilot](https://github.com/jxxghp/MoviePilot) 第三方插件仓库，插件采用 V3 结构（`package.v3.json` + `plugins.v3/`）。

## 插件列表

| 插件 | 插件 ID | 版本 | 说明 |
| --- | --- | --- | --- |
| RSS 测试 | `RssTester` | 1.0.6 | 按设置中选择的站点批量检查 RSS 订阅源，按「有效 / 链接过期 / 请求失败」三类展示结果，可推送通知。 |

## 安装

1. 打开 MoviePilot，进入「设置 → 插件市场 → 插件仓库」。
2. 添加本仓库地址：`https://github.com/jinbo-152/MoviePilot-Plugins`
3. 刷新插件市场，找到对应插件后点击安装。

本仓库同时提供 Release 安装包（见 [Releases](https://github.com/jinbo-152/MoviePilot-Plugins/releases)），在插件市场中也可以选择指定版本安装。

## 目录结构

```
package.v3.json                    # V3 插件索引：名称、版本、更新说明、release 标记
plugins.v3/<插件id>/               # 插件源码
plugins.v3/<插件id>/dist/assets/   # Vue 联邦前端产物
.github/workflows/release.yml      # 自动打包与发版工作流
```

## 发版流程

1. 修改 `plugins.v3/<插件id>/` 下的源码。
2. 更新 `package.v3.json`：提升 `version`，并在 `history` 中补充该版本的更新说明（推荐 `v1.0.7` 形式的键名，同时也兼容不带 `v` 的写法）。
3. 确认该插件条目带有 `"release": true`。
4. 推送到 `main` 分支，工作流会自动执行：

   - 将 `plugins.v3/<插件id>` 打包为 `<插件id>_v<版本>.zip`，自动排除 `__pycache__` 与 `.pyc`
   - 创建标签与 Release：`<插件ID>_v<版本>`
   - 更新说明取自 `history` 中对应版本的内容

5. 版本号未变且插件目录无改动时，只刷新已有 Release 的说明，不重复打包。

Release 的标签与资产命名必须遵循 `<插件ID>_v<版本>` 与 `<插件id小写>_v<版本>.zip`，MoviePilot 才会将其识别为可安装版本。

## 许可证

本项目基于 GPL-3.0，详见 [LICENSE](LICENSE)。