# 3x-ui fork kit — 关掉官方赞助广告位

针对 **3x-ui v3.11.0**（`MHSanaei/3x-ui`，许可证 **GPLv3**）。

## 为什么需要自己构建

广告是官方内置的付费赞助位，不是第三方改包：

- 前端组件 `SponsorCard` / `SponsorSlot`，登录页、仪表盘、侧栏三个 slot
- 面板进程运行时拉 `https://raw.githubusercontent.com/MHSanaei/sponsors/refs/heads/main/3X/sponsors.json`
- 关闭按钮只写 localStorage，TTL 86400000ms = **24 小时**，换设备要重关
- 数据库 182 项设置、26 个 `XUI_*` 环境变量里**都没有开关**
- 上游 issue #6845 / #6856 / #6818 / #6844 / #6818 提的开关全部被 **`not_planned`** 拒绝

所以只能自己打补丁。

## 补丁做了什么

`patches/0001-sponsors-env-optout.patch` 给 `cachedSponsors()` 加一个前置守卫：

```go
func cachedSponsors() (*SponsorList, error) {
	if sponsorsDisabled() {
		return &SponsorList{Sponsors: []Sponsor{}}, nil
	}
	...
```

`XUI_DISABLE_SPONSORS=true` 时直接返回空列表 → 前端 `if f.length===0 return null` → 卡片不渲染，且**完全不发出对 GitHub 的请求**。

三个设计选择，不是随手写的：

1. **默认关闭**：不设环境变量时行为与上游完全一致，因此 `sponsor_test.go` 照常通过（它自己覆盖 `sponsorsURL`，见该文件第 129 行），`ci.yml` 的 `go test` 不会挂。
2. **守卫在加锁之前**：省掉无意义的 mutex 争用。
3. **不改 `sponsorsURL` 常量**：靠 404 触发错误路径太间接，且会和测试桩冲突。

## 为什么不能本地构建

- `go.mod` 用 `github.com/mattn/go-sqlite3` → **CGO 必须开**，Windows 交叉编译到 linux/amd64 需要 musl 交叉工具链
- `internal/web/dist` **没有提交进仓库**（被 gitignore），而 `internal/web/web.go:58` 是 `//go:embed all:dist` → 必须先用 Node 26 + Vite 构建前端
- 你的 VPS 只有 426MB 内存 / 1 核 / 无 Go，构建不了
- 本机无 Docker、无 WSL

结论：**用 GitHub Actions 构建**，复用官方 `release.yml` 的构建方式（Bootlin musl 工具链 + 静态链接）。

## 步骤

```bash
# 1. 在 GitHub 上 fork MHSanaei/3x-ui（ GPLv3，自用修改无需公开；公开 fork 必须保留 GPLv3）

# 2. 克隆你的 fork，放进本 kit
git clone https://github.com/<你>/3x-ui.git && cd 3x-ui
cp -r <本目录>/patches <本目录>/.github <本目录>/scripts .

# 3. 打补丁 + 推 tag 触发构建
./scripts/sync-upstream.sh v3.11.0
#    -> 推送 tag v3.11.0-nosponsor，Actions 产出 artifact x-ui-linux-amd64-nosponsor

# 4. 下载 artifact，部署（见 deploy/deploy-binary.sh，务必先备份原二进制）

# 5. 服务器上开启开关
sudo cp systemd/20-x-ui-local.conf /etc/systemd/system/x-ui.service.d/
sudo systemctl daemon-reload && sudo systemctl restart x-ui
```

## 以后升级

每次上游发新版，跑一次 `./scripts/sync-upstream.sh v新版本号`。补丁冲突时脚本会明确报错并告诉你改哪里，不会静默产出还带广告的包——workflow 里有一步 `grep XUI_DISABLE_SPONSORS` 断言，补丁丢了构建直接失败。

## 临时替代方案（不构建的话）

`/etc/hosts` 加 `127.0.0.1 raw.githubusercontent.com`。已核实影响面：面板进程用它只做 3 件事——`sponsors.json`、赞助 logo、`update.sh`；xray 内核升级、geoip/geosite、版本提醒走 `api.github.com` / `github.com`，不受影响。代价是升级面板前要临时删掉这行。

**不要**用 systemd `IPAddressDeny`：xray 是 x-ui 的子进程、同一个 cgroup，会连带封掉代理转发到 GitHub Pages 的真实用户流量。
